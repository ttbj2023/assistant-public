# Personal Assistant - 生产环境多阶段构建 (分层优化版, 含国内镜像加速)
#
# 构建命令:
#   docker build -f docker/Dockerfile -t assistant:latest ..
#
# 国内构建默认走清华镜像 (apt + pip); 海外构建传参关闭:
#   docker build -f docker/Dockerfile --build-arg USE_CN_MIRROR=false --build-arg PIP_INDEX_URL=https://pypi.org/simple -t assistant:latest ..
#
# 分层策略:
#   1. deps 层: pyproject.toml + stub src → pip install 第三方依赖 (极少变)
#   2. 源码层: COPY src/ + entrypoint (频繁变, 秒级重建)
#
# 重型运行时依赖 (pandoc/Playwright/字体) 已迁移至 tool-runtime 附属容器

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG USE_CN_MIRROR=true

# ============================================================
# Stage 1: deps - 仅安装第三方依赖, 与源码解耦
# ============================================================
FROM python:3.12-slim AS deps

ARG PIP_INDEX_URL
ARG USE_CN_MIRROR

WORKDIR /build

RUN if [ "${USE_CN_MIRROR}" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=${PIP_INDEX_URL}

COPY pyproject.toml README.md ./

RUN mkdir src && touch src/__init__.py \
    && pip install --no-cache-dir --prefix=/install .

# ============================================================
# Stage 2: runtime - 从稳定到易变逐层排列
# ============================================================
FROM python:3.12-slim

ARG USE_CN_MIRROR

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production

WORKDIR /app

# 层2.1: 复制已安装的第三方依赖
COPY --from=deps /install /usr/local

# 层2.2: 用户和目录设置
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data

# config.yaml 已纳入版本控制 (生产/开发共用), 镜像内置兜底, 运行时由 compose 卷挂载覆盖
COPY config.yaml config.yaml
RUN chown -R appuser:appuser /app/config.yaml /app/data

# 层2.3: 源码 (频繁变更, 置于最后, 秒级重建)
COPY src/ src/

# 层2.4: Skills 资源 (SKILL.md + references + scripts, 供 SkillBridge 解析)
COPY skills/ skills/

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
