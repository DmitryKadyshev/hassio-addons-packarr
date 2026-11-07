ARG BUILD_FROM=ghcr.io/hassio-addons/base:20.2.0
FROM ${BUILD_FROM}

RUN apk add --no-cache python3
COPY src /opt/packarr
ENV PYTHONPATH=/opt/packarr PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/packarr
ENTRYPOINT ["python3", "-m", "packarr"]
CMD ["--config", "/data/options.json"]

LABEL org.opencontainers.image.source="https://github.com/DmitryKadyshev/hassio-addons-packarr" \
      org.opencontainers.image.title="Packarr" \
      org.opencontainers.image.description="Selective rolling-pack downloads for Sonarr and Transmission" \
      org.opencontainers.image.licenses="MIT"
