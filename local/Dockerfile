# syntax=docker/dockerfile:1
# Two images from one file (build both: `docker compose -f local/docker-compose.yml build`, context = repo root):
#   app     web server + build toolchain + adb, Relume code baked in (no signing keys: they are mounted at deploy time)
#   browser upstream CloudflareBypassForScraping image (named context "upstream", pinned in compose) + the APKMirror solver
# Large independent payloads live in their own stages and arrive via COPY --link, so changing one layer never invalidates the others.

FROM alpine:3.23 AS dl-pup
ARG TARGETARCH
RUN wget -q -O /tmp/pup.zip "https://github.com/ericchiang/pup/releases/download/v0.4.0/pup_v0.4.0_linux_${TARGETARCH}.zip" \
    && unzip -q /tmp/pup.zip -d /out && chmod 755 /out/pup

FROM alpine:3.23 AS dl-apkeep
ARG TARGETARCH
RUN case "$TARGETARCH" in amd64) t=x86_64 ;; arm64) t=aarch64 ;; *) echo "apkeep: no build for $TARGETARCH" >&2; exit 1 ;; esac \
    && mkdir /out && wget -q -O /out/apkeep "https://github.com/EFForg/apkeep/releases/download/1.1.0/apkeep-$t-unknown-linux-gnu" \
    && chmod 755 /out/apkeep

FROM alpine:3.23 AS dl-apkeditor
RUN wget -q -O /APKEditor.jar https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar

FROM debian:trixie-slim AS app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl wget jq unzip zip ca-certificates tzdata adb \
      openjdk-21-jre-headless python3 apksigner \
    && rm -rf /var/lib/apt/lists/*
COPY --link --from=dl-pup /out/pup /usr/local/bin/pup
COPY --link --from=dl-apkeep /out/apkeep /usr/local/bin/apkeep
COPY --link --from=dl-apkeditor /APKEditor.jar /opt/APKEditor.jar
ENV HOME=/data/home PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo/local
WORKDIR /repo
# src/build before local/webui: BuildKit chains cache keys forward, so the faster-changing webui layer must come last
COPY --link src/build /repo/src/build
COPY --link local/webui /repo/local/webui
ARG RELUME_VERSION=dev
ENV RELUME_VERSION=$RELUME_VERSION
CMD ["python3", "-m", "webui"]

FROM upstream AS browser
COPY --link local/solver /solver
