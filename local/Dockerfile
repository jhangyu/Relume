# syntax=docker/dockerfile:1
# Two images from one file (build both: `docker compose -f local/docker-compose.yml build`, context = repo root):
#   app     web server + build toolchain + adb, Relume code baked in (no signing keys: they are mounted at deploy time)
#   browser upstream CloudflareBypassForScraping image (named context "upstream", pinned in compose) + the APKMirror solver
# Large independent payloads live in their own stages and arrive via COPY --link, so changing one layer never invalidates the others.

FROM alpine:3.23 AS dl-pup
ARG TARGETARCH
RUN wget -q -O /tmp/pup.zip "https://github.com/ericchiang/pup/releases/download/v0.4.0/pup_v0.4.0_linux_${TARGETARCH}.zip" \
    && unzip -q /tmp/pup.zip -d /out && chmod 755 /out/pup

# apkeep = EFForg/apkeep 1.1.0 + local/apkeep/apkeep.patch: Google Play `-a pkg@<versionCode>` pins a version,
# `-o probe=session` answers stdin probes without downloading; --version says +relume.1.
# Built natively for the target platform: it links OpenSSL and C crypto code, so cross-compiling would need a per-arch toolchain.
FROM rust:1.99.1-trixie AS build-apkeep
ADD https://github.com/EFForg/apkeep.git#3356f137e34b50c3ae198d23d09dd2aab8799310 /src
COPY local/apkeep/apkeep.patch /src/
WORKDIR /src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    git apply apkeep.patch && cargo build --release --locked && install -D -m 755 target/release/apkeep /out/apkeep

FROM alpine:3.23 AS dl-apkeditor
RUN wget -q -O /APKEditor.jar https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar

FROM debian:trixie-slim AS app
ENV DEBIAN_FRONTEND=noninteractive
# build-time apt mirror (NCHC has no debian-security, that stays official); restored in the same RUN so the image keeps stock sources
ARG DEBIAN_MIRROR=http://free.nchc.org.tw/debian
RUN sed -i "s#http://deb.debian.org/debian\$#$DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
      bash curl wget jq unzip zip ca-certificates tzdata adb libssl3t64 \
      openjdk-21-jre-headless python3 apksigner \
    && sed -i "s#$DEBIAN_MIRROR#http://deb.debian.org/debian#" /etc/apt/sources.list.d/debian.sources \
    && rm -rf /var/lib/apt/lists/*
COPY --link --from=dl-pup /out/pup /usr/local/bin/pup
COPY --link --from=build-apkeep /out/apkeep /usr/local/bin/apkeep
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
