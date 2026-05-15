FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packet_capture_monitor ./packet_capture_monitor

RUN pip install .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /home/appuser/.mitmproxy \
    && chown -R appuser:appuser /app /home/appuser/.mitmproxy

USER appuser

EXPOSE 8765 8081

CMD ["packet-monitor", "server", "--host", "0.0.0.0", "--port", "8765"]
