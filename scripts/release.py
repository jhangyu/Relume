#!/usr/bin/env python3
"""Build, deploy, verify and push the Relume images.

Steps: preflight -> compose build -> compose up -d --wait -> all containers healthy
-> delete dangling Relume images -> arch check -> tag + push -> local git tag v<version>.
Usage: python3 scripts/release.py MAJOR.MINOR.PATCH [--no-push]
"""
import argparse
import json
import os
import re
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
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
REQUIRED_PLATFORM = "linux/arm64"


def run(*cmd: str, capture: bool = False) -> str:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, cwd=ROOT, text=True, stdout=subprocess.PIPE if capture else None).stdout or ""


def fail(msg: str) -> None:
    sys.exit(f"release.py: {msg}")


def parse_version(text: str) -> str:
    if not VERSION_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError(f"version must be MAJOR.MINOR.PATCH (digits only), got {text!r}")
    return text


def build_context_dirty() -> str:
    return run("git", "status", "--porcelain", "--", *BUILD_CONTEXT, capture=True).strip()


def head_sha() -> str:
    return run("git", "rev-parse", "HEAD", capture=True).strip()


def check_tree_unchanged(built_sha: str, current_sha: str, dirty: str) -> None:
    if current_sha != built_sha:
        fail(f"HEAD moved during the release: expected {built_sha}, got {current_sha}; images are pushed, create the git tag manually on {built_sha}")
    if dirty:
        fail(f"build-context files changed during the release, images may not match {built_sha}; images are pushed, no git tag created:\n{dirty}")


def preflight(version: str, push: bool) -> str:
    run("docker", "info", "--format", "{{.ServerVersion}}", capture=True)  # non-zero when the daemon is down
    if run("git", "tag", "--list", f"v{version}", capture=True).strip():
        fail(f"git tag v{version} already exists: expected an unused version, pick a new one or delete the local tag")
    if push:
        auths = json.loads(DOCKER_CONFIG.read_text()).get("auths", {})
        if HUB_AUTH_KEY not in auths:
            fail(f"not logged in to Docker Hub: expected {HUB_AUTH_KEY} in {DOCKER_CONFIG} auths, got {sorted(auths)}; run `docker login`")
        # the tag names a commit: a dirty tree would push content that hash does not describe
        dirty = build_context_dirty()
        if dirty:
            fail(f"build-context files are modified or untracked, commit first (a pushed tag must match HEAD):\n{dirty}")
    return head_sha()


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


def check_platform(image: str, actual: str) -> None:
    if actual != REQUIRED_PLATFORM:
        fail(f"image {image} is {actual or 'unknown'}: expected {REQUIRED_PLATFORM}; nothing was pushed")


def push(version: str, built_sha: str) -> None:
    for image in IMAGES:
        check_platform(image, run("docker", "image", "inspect", "-f", "{{.Os}}/{{.Architecture}}", f"{image}:latest", capture=True).strip())
    for image in IMAGES:
        run("docker", "tag", f"{image}:latest", f"{image}:{version}")
    # versions first: a failure never leaves latest pointing at a mismatched app/browser pair
    for tag in (version, "latest"):
        for image in IMAGES:
            run("docker", "push", f"{image}:{tag}")
    check_tree_unchanged(built_sha, head_sha(), build_context_dirty())
    run("git", "tag", "-a", f"v{version}", built_sha, "-m", f"Relume {version}")  # local only, git is never pushed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", type=parse_version, help="MAJOR.MINOR.PATCH, baked into the app and used as the image tag")
    parser.add_argument("--no-push", action="store_true", help="build, deploy and clean up, but do not push")
    args = parser.parse_args()
    built_sha = preflight(args.version, push=not args.no_push)
    os.environ["RELUME_VERSION"] = args.version  # compose reads it as the build arg
    run(*COMPOSE, "build")
    run(*COMPOSE, "up", "-d", "--wait")
    check_healthy()
    remove_dangling()
    if not args.no_push:
        push(args.version, built_sha)


if __name__ == "__main__":
    main()
