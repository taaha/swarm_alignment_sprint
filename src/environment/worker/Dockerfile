ARG SOURCE_IMAGE
ARG PYTHON_IMAGE
FROM ${SOURCE_IMAGE} AS application
COPY prepare.py /tmp/prepare.py
RUN python /tmp/prepare.py

FROM ${PYTHON_IMAGE}
COPY --from=application /usr/local/ /usr/local/
WORKDIR /app/oscar
COPY --from=application --chown=1000:1000 /app/oscar/src/ ./src/
COPY --from=application --chown=1000:1000 /app/oscar/tests/ ./tests/
COPY --from=application --chown=1000:1000 /app/oscar/shop/ ./shop/
COPY --from=application --chown=1000:1000 /app/oscar/gulpfile.js/ ./gulpfile.js/
COPY --from=application --chown=1000:1000 /app/oscar/pyproject.toml /app/oscar/setup.py /app/oscar/setup.cfg /app/oscar/package.json /app/oscar/package-lock.json /app/oscar/LICENSE /app/oscar/MANIFEST.in /app/oscar/README.rst ./
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home app \
    && chown 1000:1000 /app/oscar
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/oscar:/app/oscar/shop DJANGO_SETTINGS_MODULE=settings \
    HOME=/home/app USER=app
LABEL org.opencontainers.image.title="Oscar shop" \
    org.opencontainers.image.version="local-v1"
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=5s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"
CMD ["python", "shop/manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
