FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG http_proxy=
ARG https_proxy=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    XDG_CACHE_HOME=/tmp/.cache \
    HOME=/tmp \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN groupadd --gid 10002 toolrt \
    && useradd --uid 10002 --gid toolrt --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin toolrt

RUN pip install \
    "fastapi>=0.104,<1" \
    "uvicorn[standard]>=0.24,<1" \
    "playwright>=1.50.0" \
    "openpyxl>=3.1,<4" \
    "numpy>=2.0,<3" \
    "pandas>=2.2,<3"

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        libreoffice-calc \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
    && python -m playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/tool-runtime/app.py /app/app.py
COPY docker/tool-runtime/chart_builder.py /app/chart_builder.py
COPY docker/tool-runtime/chart_libs/ /app/chart_libs/
COPY skills/ /skills/

RUN mkdir -p /workspace/output && chown -R toolrt:toolrt /app /workspace /skills

USER toolrt:toolrt

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${TOOL_RUNTIME_PORT:-8766} --proxy-headers --no-server-header"]
