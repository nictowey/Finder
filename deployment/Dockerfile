# Static cloud runtime foundation; not deployed or container-verified.
# Pin an independently verified base digest before production deployment.
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels '.[cloud]'

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 finder && useradd --uid 10001 --gid 10001 --no-create-home finder \
    && mkdir /data && chown 10001:10001 /data
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
USER 10001:10001
WORKDIR /data
EXPOSE 8080
# TLS must terminate at an approved gateway. No provider/credential/browser setup here.
# Missing owner/auth/origin/database config fails closed at factory startup.
CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:${PORT:-8080} --threads=1 --max-request-body-size=65536 --max-request-header-size=16384 --channel-timeout=15 --no-expose-tracebacks --call finder.cloud_app:create_app"]
