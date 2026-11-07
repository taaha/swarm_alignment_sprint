ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE} AS development
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/oscar
COPY constraints.txt requirements.lock /opt/environment/
RUN pip install -c /opt/environment/constraints.txt -r /opt/environment/requirements.lock
COPY oscar/ /app/oscar/
RUN pip install --no-deps --no-build-isolation -e '.[test]'
CMD ["python", "sandbox/manage.py", "runserver", "0.0.0.0:8000", "--noreload"]

FROM development AS baseline
COPY seed.py /opt/environment/seed.py
RUN npm ci --no-audit --no-fund \
    && npm run build \
    && python /opt/environment/seed.py --reset \
    && python sandbox/manage.py check \
    && rm -rf node_modules
COPY baseline_settings.py /opt/environment/baseline_settings.py
ENV PYTHONPATH=/opt/environment:/app/oscar/sandbox \
    DJANGO_SETTINGS_MODULE=baseline_settings
LABEL org.opencontainers.image.title="Oscar experiment baseline v1" \
    org.opencontainers.image.version="baseline-v1" \
    org.opencontainers.image.source="https://github.com/django-oscar/django-oscar" \
    org.opencontainers.image.revision="d0a70850abf8e4229d4971aa7e8f4643326ec9f3"
HEALTHCHECK --interval=5s --timeout=5s --start-period=5s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"
