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
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    HOME=/tmp

# 非 root 用户 (uid 10003, 与 tool-runtime 错开)
RUN groupadd --gid 10003 mkmon \
    && useradd --uid 10003 --gid mkmon --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin mkmon

# 行情查询服务依赖: 纯查询无状态, 不含派发(aiosmtplib)与规则存储
RUN pip install \
    "fastapi>=0.104,<1" \
    "uvicorn[standard]>=0.24,<1" \
    "httpx>=0.27,<1" \
    "pytdx>=1.72,<2"

WORKDIR /app
COPY docker/quote-service/*.py /app/

RUN chown -R mkmon:mkmon /app

USER mkmon:mkmon

ENV QUOTE_SERVICE_PORT=8767

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${QUOTE_SERVICE_PORT:-8767} --proxy-headers --no-server-header"]
