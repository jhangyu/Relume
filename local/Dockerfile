FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl wget jq unzip zip tar git ca-certificates \
      openjdk-21-jdk-headless python3 python3-venv python-is-python3 apksigner \
    && rm -rf /var/lib/apt/lists/*
COPY src/build/helper/ggplay_dl/requirements.txt /opt/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /opt/requirements.txt
# venv first on PATH so `python` (utils.sh get_apk via ggplay_dl) sees the requirements
ENV PATH=/opt/venv/bin:$PATH
CMD ["sleep", "infinity"]
