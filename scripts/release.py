#!/usr/bin/env python3
"""Build, deploy, verify and push the Relume images.

Steps: preflight -> compose build -> compose up -d --wait -> all containers healthy
-> delete dangling Relume images -> tag + push. Usage: python3 scripts/release.py [--no-push]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# paths the Dockerfile copies into the images: any change or untracked file here ends up in a pushed image
BUILD_CONTEXT = ["local/Dockerfile", "local/Dockerfile.dockerignore", "local/docker-compose.yml", "local/webui", "local/solver", "src/build"]
ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "local" / "docker-compose.yml")]
PROJECT = "relume"
IMAGES = ["jhangyu/relume-app", "jhangyu/relume-browser"]
DOCKER_CONFIG = Path.home() / ".docker" / "config.json"
HUB_AUTH_KEY = "https://index.docker.io/v1/"
# compose stamps this label on every image it builds, and it survives when the image loses its tag
PROJECT_LABEL = f"com.docker.compose.project={PROJECT}"


def run(*cmd: str, capture: bool = False) -> str:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, cwd=ROOT, text=True, stdout=subprocess.PIPE if capture else None).stdout or ""


def fail(msg: str) -> None:
    sys.exit(f"release.py: {msg}")


def preflight(push: bool) -> str:
    run("docker", "info", "--format", "{{.ServerVersion}}", capture=True)  # non-zero when the daemon is down
    if push:
        auths = json.loads(DOCKER_CONFIG.read_text()).get("auths", {})
        if HUB_AUTH_KEY not in auths:
            fail(f"not logged in to Docker Hub: expected {HUB_AUTH_KEY} in {DOCKER_CONFIG} auths, got {sorted(auths)}; run `docker login`")
        # the tag names a commit: a dirty tree would push content that hash does not describe
        dirty = run("git", "status", "--porcelain", "--", *BUILD_CONTEXT, capture=True).strip()
        if dirty:
            fail(f"build-context files are modified or untracked, commit first (a pushed tag must match HEAD):\n{dirty}")
    return run("git", "rev-parse", "--short", "HEAD", capture=True).strip()


def check_healthy() -> None:
    out = run(*COMPOSE, "ps", "--all", "--format", "json", capture=True)
    containers = [json.loads(line) for line in out.splitlines() if line.strip()]
    if not containers:
        fail(f"no containers found for compose project {PROJECT}")
    bad = [f"{c['Service']}: State={c['State']} Health={c.get('Health') or 'none'} (expected running, healthy or no healthcheck)"
           for c in containers if c["State"] != "running" or c.get("Health") not in ("", None, "healthy")]
    if bad:
        fail("unhealthy containers:\n" + "\n".join(bad))


def remove_dangling() -> None:
    ids = run("docker", "image", "ls", "-q", "--filter", "dangling=true", "--filter", f"label={PROJECT_LABEL}", capture=True).split()
    if ids:
        run("docker", "image", "rm", *ids)


def push(commit: str) -> None:
    for image in IMAGES:
        run("docker", "tag", f"{image}:latest", f"{image}:{commit}")
        for tag in ("latest", commit):
            run("docker", "push", f"{image}:{tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-push", action="store_true", help="build, deploy and clean up, but do not push")
    args = parser.parse_args()
    commit = preflight(push=not args.no_push)
    run(*COMPOSE, "build")
    run(*COMPOSE, "up", "-d", "--wait")
    check_healthy()
    remove_dangling()
    if not args.no_push:
        push(commit)


if __name__ == "__main__":
    main()
