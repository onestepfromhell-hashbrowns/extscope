# ==============================================================================
# ExtScope Portable Container Image (Standalone, FHS-Compliant)
# Ready for Linux, Docker, Podman, and Kubernetes.
# ==============================================================================
FROM docker.io/library/python:3.12-slim-bookworm AS base

# 1. 設置環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    EXTSCOPE_MODE=system \
    XDG_CONFIG_HOME=/etc/extscope \
    XDG_DATA_HOME=/var/lib/extscope \
    XDG_RUNTIME_DIR=/run/extscope

# 2. 建立符合 FHS 標準之系統目錄
RUN mkdir -p /etc/extscope \
             /var/lib/extscope/canonical \
             /var/cache/extscope \
             /run/extscope \
             /app

# 3. 建立低權限專用執行用戶 (UID 1000)
RUN groupadd -g 1000 extscope && \
    useradd -u 1000 -g extscope -d /var/lib/extscope -s /bin/bash extscope && \
    chown -R extscope:extscope /etc/extscope /var/lib/extscope /var/cache/extscope /run/extscope /app

# 4. 安裝套件
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir /app

# 5. 切換至安全使用者
USER extscope
WORKDIR /var/lib/extscope

HEALTHCHECK --interval=60s --timeout=5s --start-period=5s --retries=3 \
    CMD ["extscope", "--help"]

# 6. 入口命令
ENTRYPOINT ["extscope"]
CMD ["--help"]
