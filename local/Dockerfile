# app image: web server + build toolchain + adb (spec core §1, build §2.1, device D1/D2). Code is bind-mounted.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl wget jq unzip zip ca-certificates tzdata adb \
      openjdk-21-jdk-headless python3 apksigner \
    && rm -rf /var/lib/apt/lists/*
RUN case "$(dpkg --print-architecture)" in arm64) a=arm64 ;; amd64) a=amd64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac \
    && wget -q -O /tmp/pup.zip "https://github.com/ericchiang/pup/releases/download/v0.4.0/pup_v0.4.0_linux_${a}.zip" \
    && unzip -q /tmp/pup.zip -d /usr/local/bin && rm /tmp/pup.zip && chmod 755 /usr/local/bin/pup \
    && wget -q -O /opt/APKEditor.jar https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar
ENV HOME=/data/home PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo/local
WORKDIR /repo
CMD ["python3", "-m", "webui"]
