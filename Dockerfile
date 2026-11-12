# ==============================================================================
# ExtScope Portable Container Image (Standalone, FHS-Compliant)
# No site-specific assumptions. Ready for Linux, Docker, Podman, and Kubernetes.
# ==============================================================================
FROM python:3.12-slim-bookworm AS base

# 1. environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    EXTSCOPE_MODE=system \
    XDG_CONFIG_HOME=/etc/extscope \
    XDG_DATA_HOME=/var/lib/extscope \
    XDG_RUNTIME_DIR=/run/extscope

# 2. FHS-style system directories
RUN mkdir -p /etc/extscope \
             /var/lib/extscope \
             /var/cache/extscope \
             /run/extscope \
             /app

# 3. unprivileged runtime user (UID 1000)
RUN groupadd -g 1000 extscope && \
    useradd -u 1000 -g extscope -d /var/lib/extscope -s /bin/bash extscope && \
    chown -R extscope:extscope /etc/extscope /var/lib/extscope /var/cache/extscope /run/extscope /app

# 4. install the package
WORKDIR /app
COPY pyproject.toml /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir /app

# 5. switch to the unprivileged user
USER extscope
WORKDIR /var/lib/extscope

# 6. entrypoint
ENTRYPOINT ["extscope"]
CMD ["--help"]
