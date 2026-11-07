ARG BASELINE_IMAGE=swarm-oscar:baseline-v1
FROM ${BASELINE_IMAGE}
COPY workspace/source/ /app/oscar/src/oscar/
COPY settings.py /opt/environment/experiment_settings.py
ENV DJANGO_SETTINGS_MODULE=experiment_settings
LABEL org.opencontainers.image.title="Oscar experiment starting point" \
    org.opencontainers.image.version="experiment-ready-v1" \
    swarm-alignment.stage="ready" \
    swarm-alignment.applied-bug-count="0"
