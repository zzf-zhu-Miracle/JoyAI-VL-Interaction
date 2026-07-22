#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
codex_version="$(npm view @openai/codex version)"
build_args=()

for name in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy \
  PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST; do
  if [[ -n "${!name:-}" ]]; then
    build_args+=(--build-arg "$name=${!name}")
  fi
done

build() {
  local image="$1"
  local dockerfile="$2"
  shift 2
  docker build --network host "${build_args[@]}" "$@" -f "$root/$dockerfile" -t "$image" "$root"
}

docker image inspect python:3.12-slim-bookworm >/dev/null
docker image inspect node:22-bookworm-slim >/dev/null
build joyai-vl-app:latest Dockerfile.app
build joyai-vl-background-model:latest Dockerfile.background --build-arg "CODEX_BUILD_VERSION=$codex_version"
