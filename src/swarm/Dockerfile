FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/controller
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY swarm ./swarm
COPY prompts ./prompts
COPY tests ./tests
COPY issues.json ./issues.json
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SWARM_DATA=/state
ENTRYPOINT ["python", "-m", "swarm.cli"]
