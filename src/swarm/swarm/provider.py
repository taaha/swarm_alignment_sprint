import time
import httpx


class ProviderError(RuntimeError):
    pass


class Ollama:
    def __init__(self, host, key, telemetry):
        self.http = httpx.Client(base_url=host.rstrip('/').removesuffix('/v1'),
            headers={'Authorization':'Bearer '+key},timeout=httpx.Timeout(300,connect=20))
        self.telemetry = telemetry

    def check(self, model, thinking):
        response = self.http.post('/api/show',json={'model':model})
        response.raise_for_status()
        metadata = response.json()
        if 'tools' not in metadata.get('capabilities',[]):
            raise ValueError('Selected model does not advertise tool calling')
        if thinking is not False and 'thinking' not in metadata.get('capabilities',[]):
            raise ValueError('Selected model does not advertise thinking')
        values = metadata.get('thinking',{}).get('values',[])
        if values and thinking not in values:
            raise ValueError('Unsupported thinking value for selected model: '+str(thinking))
        self.telemetry.event('model_capabilities',dict(model=model,thinking=metadata.get('thinking'),capabilities=metadata.get('capabilities')))

    def chat(self, model, thinking, messages, tools, config):
        payload = dict(model=model,messages=messages,tools=tools,think=thinking,stream=False,
                       options=dict(temperature=config['temperature'],num_predict=config['max_tokens']))
        with self.telemetry.span('ollama_chat','LLM') as span:
            span.set_inputs(self.telemetry.clean(payload))
            span.set_attribute('model',model)
            span.set_attribute('thinking',thinking)
            for attempt in range(3):
                started = time.monotonic()
                self.telemetry.event('model_request',dict(attempt=attempt,request=payload))
                try:
                    response = self.http.post('/api/chat',json=payload)
                    response.raise_for_status()
                except (httpx.TransportError,httpx.HTTPStatusError) as error:
                    status = error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None
                    self.telemetry.event('model_error',dict(attempt=attempt,status=status,error=str(error)))
                    if attempt == 2 or status is not None and status not in [408,429,500,502,503,504]:
                        raise
                    time.sleep(min(2**attempt,4))
                    continue
                data = response.json()
                seconds = time.monotonic()-started
                self.telemetry.event('model_response',dict(response=data,latency_seconds=seconds))
                self.telemetry.usage(data,seconds)
                span.set_attribute('mlflow.chat.tokenUsage',dict(input_tokens=data.get('prompt_eval_count',0),output_tokens=data.get('eval_count',0),total_tokens=data.get('prompt_eval_count',0)+data.get('eval_count',0)))
                span.set_outputs(self.telemetry.clean(data))
                if data.get('error'):
                    raise ProviderError(data['error'])
                return data
