FROM python:3.11-slim

ARG APT_MIRROR=
ARG APT_SECURITY_MIRROR=
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "$APT_SECURITY_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org/debian-security|$APT_SECURITY_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packet_capture_monitor ./packet_capture_monitor

RUN set -eux; \
    python -m pip install --upgrade pip setuptools wheel; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      python -m pip config set global.index-url "$PIP_INDEX_URL"; \
    fi; \
    if [ -n "$PIP_EXTRA_INDEX_URL" ]; then \
      python -m pip config set global.extra-index-url "$PIP_EXTRA_INDEX_URL"; \
    fi; \
    python -m pip install . \
    || python -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple . \
    || python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /home/appuser/.mitmproxy \
    && chown -R appuser:appuser /app /home/appuser/.mitmproxy

USER appuser

EXPOSE 8765 8081

CMD ["packet-monitor", "server", "--host", "0.0.0.0", "--port", "8765"]
