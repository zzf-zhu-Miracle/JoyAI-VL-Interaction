# Getting Started

> 中文文档: [getting_started.zh-CN.md](./getting_started.zh-CN.md)

## Prerequisites

- Linux (no local GPU required — inference runs via the Bailian cloud API)
- Python 3.12 (recommended)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip for Python package management
- An Alibaba Bailian (DashScope) API key for the Qwen-Omni-Realtime API

## Installation

Use the provided install script to set up all dependencies:

```bash
# Install WebUI + background agent dependencies
./install/install.sh --with-all
```

For compatibility notes across platforms, see `install/README.md`.

`install/` is only for dependency setup and generated configuration.
Runtime entrypoints live under `services/`: use `services/scripts/run.sh` for orchestration, or
`services/<service>/scripts/` for component-level commands.

## Omni Realtime API Configuration

Inference runs via the Alibaba Bailian Qwen-Omni-Realtime cloud API. Configure it through
environment variables before starting the WebUI:

```bash
export DASHSCOPE_API_KEY=sk-...   # required
# Optional overrides (built-in defaults exist):
# export OMNI_REALTIME_URL=wss://...
# export OMNI_MODEL=qwen3.5-omni-flash-realtime
```

## Minimal Setup (WebUI only)

Start only the web UI:

```bash
./services/scripts/run.sh minimal
```

`services/scripts/run.sh minimal` keeps WebUI in the foreground.
Press `Ctrl+C` in that terminal to stop the services started by the orchestrator.

Open your browser at: `https://127.0.0.1:8099`

## Full Setup (all services)

The background agent must start **before** the web UI.

### Recommended startup order

```text
1. background-agent           (optional — enables task delegation)
2. services/webui             (required — start last)
```

### Step-by-step

If you keep the optional `background-agent` enabled, prepare `CODEX_HOME` first and
read [the background-agent README](../services/background-agent/README.md). Either point
`CODEX_HOME` at an existing Codex home or copy `auth.json` and `config.toml` into the
default service path:

```bash
# Option 1: point background-agent at an existing Codex home
export CODEX_HOME=/path/to/your/codex-home

# Option 2: use the default service Codex home
mkdir -p services/background-agent/codex-home
cp /path/to/your/codex-home/{auth.json,config.toml} services/background-agent/codex-home/
```

Start the full service set with one command:

```bash
./services/scripts/run.sh all
```

`services/scripts/run.sh all` starts optional services before WebUI. Use
`START_BACKGROUND_AGENT=0` to skip the background agent.
When `background-agent` is enabled, its `CODEX_HOME` must contain `config.toml` and `auth.json`.
When using this orchestrator, the full service set is not ready until the final WebUI process has started.
Press `Ctrl+C` in that terminal to stop the services started by the orchestrator.

You can also start the services manually in this order:

```bash
# 1. Background Agent (optional)
./services/background-agent/scripts/run.sh

# 2. Web UI (start last)
source services/.venv/bin/activate
(cd services/webui && bash scripts/start_server.sh)
```

## Health Checks

After startup, verify each service is running:

```bash
curl http://127.0.0.1:8079/health   # background-agent (optional)
```

The web UI is accessible at `https://127.0.0.1:8099` (accept the self-signed certificate warning).

## RTSP Local Stream Testing

The WebUI can use either a webcam or an RTSP input. To test RTSP without a physical IP camera, you can run a local MediaMTX server and push a local video file with `ffmpeg`.

After the local stream is running, enter an RTSP URL such as `rtsp://127.0.0.1:8554/fire1` in the WebUI RTSP input. If WebUI is running on another machine, replace `127.0.0.1` with the machine that runs MediaMTX.

See the [RTSP Local Streaming Guide](rtsp_streaming.md) for MediaMTX download notes, helper script examples, and troubleshooting checks.

## Stopping Services

If you started with `services/scripts/run.sh minimal` or `services/scripts/run.sh all`, press `Ctrl+C`
in that terminal. To stop services from another terminal:

```bash
./services/scripts/stop.sh all
```

## Configuration

### Omni realtime API (WebUI)

Key environment variables (export before launching the WebUI):

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHSCOPE_API_KEY` | — | Bailian (DashScope) API key; required |
| `OMNI_REALTIME_URL` | built-in Bailian realtime endpoint | Omni realtime WebSocket endpoint |
| `OMNI_MODEL` | `qwen3.5-omni-flash-realtime` | Realtime model name |
| `OMNI_VOICE` | `Ethan` | TTS voice used by the Omni session |
| `VENV_ACTIVATE` | auto-detects `services/.venv` | Optional venv activate script path; set `VENV_ACTIVATE=` to use the current shell environment |

### background-agent

The background agent wraps the local `codex` CLI for delegated tasks. Its default runtime
configuration is under `services/background-agent`:

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEX_HOME` | `services/background-agent/codex-home` | Codex home containing `config.toml` and `auth.json`; set this to an existing Codex home or copy those files into the default path |
| `CODEX_API_WORKSPACE` | `<repo>/agent-workspace` | Workspace used by background Codex runs; created on startup |
| `CODEX_API_HOST` | `127.0.0.1` | background-agent bind host |
| `CODEX_API_PORT` | `8079` | background-agent listen port |
| `CODEX_API_MAX_SUBAGENTS` | `6` | Maximum parallel subagents used by the API wrapper |
| `BACKGROUND_AGENT_API_URL` | `http://127.0.0.1:8079` | URL used by WebUI to reach the background-agent |

The default `CODEX_HOME` path is service-local so the background agent can run independently
from your shell. For first-time setup, either export `CODEX_HOME=/path/to/your/codex-home`
before launch, or copy `auth.json` and `config.toml` into
`services/background-agent/codex-home/`.

## Troubleshooting

For common startup and runtime issues, see the [Troubleshooting Guide](troubleshooting.md).
It covers Omni API connectivity, local image paths, and `background-agent` `CODEX_HOME` setup.
