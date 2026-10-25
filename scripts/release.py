#!/usr/bin/env python3
"""Build, deploy, verify and push the Relume images.

Steps: preflight -> compose build -> verification stack (project relume-dev) up --wait -> all containers healthy and
/api/health reports the version -> stack down -> delete unused dangling Relume images -> arch check -> tag + push ->
local git tag v<version>. The verification stack publishes RELEASE_WEB_PORT so it runs next to the Portainer stack.
Usage: python3 scripts/release.py [MAJOR.MINOR.PATCH] [--no-push]
Without a version it re-releases the newest local v* git tag. A version that was released before is pushed again:
its image tags on Docker Hub and the local git tag are overwritten.
"""
import argparse
import json
import urllib.request
import os
import re
import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_DATA = ROOT / "local" / "verify"  # data dir of the verification stack, deleted after a passed check
COMPOSE = ["docker", "compose", "-f", str(ROOT / "local" / "docker-compose.yml")]
PROJECT = "relume-dev"
RELEASE_WEB_PORT = "47010"  # the Portainer stack owns 47000
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


def current_version() -> str:
    tags = run("git", "tag", "--list", "v*", "--sort=-v:refname", capture=True).split()
    versions = [t[1:] for t in tags if VERSION_PATTERN.fullmatch(t[1:])]
    if not versions:
        fail(f"no version given and no vMAJOR.MINOR.PATCH git tag found: expected one, got tags {tags}")
    return versions[0]


def head_sha() -> str:
    return run("git", "rev-parse", "HEAD", capture=True).strip()


def check_head_unchanged(built_sha: str, current_sha: str) -> None:
    if current_sha != built_sha:
        fail(f"HEAD moved during the release: expected {built_sha}, got {current_sha}; images are pushed, create the git tag manually on {built_sha}")


def preflight(version: str, push: bool) -> str:
    run("docker", "info", "--format", "{{.ServerVersion}}", capture=True)  # non-zero when the daemon is down
    if push:
        auths = json.loads(DOCKER_CONFIG.read_text()).get("auths", {})
        if HUB_AUTH_KEY not in auths:
            fail(f"not logged in to Docker Hub: expected {HUB_AUTH_KEY} in {DOCKER_CONFIG} auths, got {sorted(auths)}; run `docker login`")
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


def check_version(version: str) -> None:
    url = f"http://127.0.0.1:{RELEASE_WEB_PORT}/api/health"
    actual = json.load(urllib.request.urlopen(url, timeout=10)).get("version")
    if actual != version:
        fail(f"{url} reports version {actual!r}: expected {version!r}")


def remove_dangling() -> None:
    ids = run("docker", "image", "ls", "-q", "--no-trunc", "--filter", "dangling=true", "--filter", f"label={PROJECT_LABEL}", capture=True).split()
    containers = run("docker", "ps", "-aq", capture=True).split()
    used = set(run("docker", "inspect", "-f", "{{.Image}}", *containers, capture=True).split()) if containers else set()
    # an old image still run by the Portainer stack stays until that stack is redeployed; the next release removes it
    for kept in sorted(set(ids) & used):
        print(f"keeping dangling image {kept[7:19]}: still used by a container", flush=True)
    if unused := [i for i in ids if i not in used]:
        run("docker", "image", "rm", *unused)


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
    check_head_unchanged(built_sha, head_sha())
    run("git", "tag", "-a", "-f", f"v{version}", built_sha, "-m", f"Relume {version}")  # local only, git is never pushed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", type=parse_version,
                        help="MAJOR.MINOR.PATCH, baked into the app and used as the image tag; default: newest v* git tag")
    parser.add_argument("--no-push", action="store_true", help="build, deploy and clean up, but do not push")
    args = parser.parse_args()
    args.version = args.version or current_version()
    print(f"release.py: version {args.version}", flush=True)
    built_sha = preflight(args.version, push=not args.no_push)
    os.environ["RELUME_VERSION"] = args.version  # compose reads it as the build arg
    os.environ["RELUME_WEB_PORT"] = RELEASE_WEB_PORT  # overrides local/.env
    run(*COMPOSE, "build")
    # the verification stack gets its own fresh data dir, never a real stack's; a failed run leaves it for
    # inspection and the next run starts it over
    shutil.rmtree(VERIFY_DATA, ignore_errors=True)
    for sub in ("work", "apk-cache", "upstream"):  # Compose does not create bind sources
        (VERIFY_DATA / sub).mkdir(parents=True)
    os.environ["RELUME_DATA_DIR"] = str(VERIFY_DATA)  # overrides local/.env
    os.environ["RELUME_GITHUB_TOKEN_FILE"] = "/dev/null"
    run(*COMPOSE, "up", "-d", "--wait")
    check_healthy()
    check_version(args.version)
    run(*COMPOSE, "down")
    shutil.rmtree(VERIFY_DATA)
    remove_dangling()
    if not args.no_push:
        push(args.version, built_sha)


if __name__ == "__main__":
    main()
