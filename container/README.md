# Container Deployment

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

One Docker Compose stack runs the two remaining services: the WebUI (browser frontend +
bridge to the Alibaba Bailian Qwen-Omni-Realtime cloud API) and the background agent
(FastAPI wrapper around the Codex CLI). Inference runs in the cloud, so no GPU, model
weights, or vLLM images are needed.

The historical `regular` / `24GB` / `16GB` GPU profiles are kept for CLI compatibility,
but they are now identical — any of them starts the same stack. Starting one profile stops
the other two.

## Requirements

- Linux with Docker Engine and Docker Compose.
- Free host ports: `8079` and `8099`.
- An Alibaba Bailian (DashScope) API key for the Qwen-Omni-Realtime API.

## Quick Start

From the repository root:

```bash
# 1. Pull the base images and build service images.
docker pull python:3.12-slim-bookworm
docker pull node:22-bookworm-slim
./container/scripts/build-images.sh

# 2. Create and edit one profile configuration (set DASHSCOPE_API_KEY).
cp container/regular/.env.example container/regular/.env
${EDITOR:-vi} container/regular/.env

# 3. Start and verify every endpoint.
./container/manage.sh regular up
./container/manage.sh regular test
```

Open `https://<host>:8099`. The WebUI uses a generated self-signed certificate.

## Operations

```bash
./container/manage.sh regular status
./container/manage.sh regular logs
./container/manage.sh regular restart
./container/manage.sh regular down
```

Replace `regular` with `24GB` or `16GB` — all profiles are equivalent.

## Configuration

- `DASHSCOPE_API_KEY`: Bailian (DashScope) API key; required.
- `OMNI_REALTIME_URL`: Omni realtime WebSocket endpoint; a default is built in.
- `OMNI_MODEL`: realtime model name; default `qwen3.5-omni-flash-realtime`.
- `WEBUI_PORT`: WebUI listen port; default `8099`.
- `BACKGROUND_AGENT_PORT`: background-agent listen port; default `8079`.
- `CODEX_HOME_HOST`: Codex configuration source; default is `../services/background-agent/codex-home`.
- `BACKGROUND_WORKSPACE_HOST`: writable background-agent workspace.

Relative paths are resolved from `container/docker-compose.yml`. Keep `.env` files local and
never commit API keys or Codex credentials. Exact image versions are recorded in `images.lock`.
