# JoyVL Installation Notes

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Inference now runs through the Alibaba Bailian Qwen-Omni-Realtime cloud API. A fresh install
only sets up the two remaining runtime services — the WebUI (browser frontend + bridge to the
cloud API) and the background agent (FastAPI wrapper around the Codex CLI). No vLLM, PyTorch,
model weights, or audio runtime are installed locally.

Unless stated otherwise, run the commands below from the repository root.

## Core Installation

- `install.sh` creates the virtual environment with `uv venv`, then installs dependencies with `uv pip install`.
- `install.sh` installs the WebUI in editable mode (aiohttp, aiortc, websockets, Pillow, etc.).
- This install directory standardizes on Python 3.12.

```bash
./install/install.sh
```

## Omni Realtime API Configuration

The WebUI connects to the Bailian Omni realtime API via environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | yes | Bailian (DashScope) API key |
| `OMNI_REALTIME_URL` | no | Omni realtime WebSocket endpoint (built-in default) |
| `OMNI_MODEL` | no | Realtime model name (default `qwen3.5-omni-flash-realtime`) |
| `OMNI_VOICE` | no | Realtime output voice (default `Ethan`) |
| `OMNI_INSTRUCTIONS` | no | Realtime system instructions (built-in Chinese default) |
| `OMNI_TURN_DETECTION` | no | `semantic_vad` (default) / `server_vad` / `none` |
| `PROACTIVE_ENABLED` | no | Enable proactive alerts / 主动播报 (default `true`) |
| `PROACTIVE_API_BASE` | no | Chat-completions endpoint for the watcher (built-in default) |
| `PROACTIVE_MODEL` | no | Watcher model name (default `qwen3.5-omni-flash`) |
| `PROACTIVE_INTERVAL_S` | no | Watcher check interval in seconds (default `3.0`) |
| `PROACTIVE_MAX_TOKENS` | no | Watcher max output tokens (default `128`) |
| `PROACTIVE_COOLDOWN_S` | no | Min seconds between injected alerts (default `15`) |
| `PROACTIVE_SYSTEM_PROMPT` | no | Watcher system prompt (built-in Chinese default) |

```bash
export DASHSCOPE_API_KEY=sk-...
```

## Background Agent

Install:

```bash
./install/install.sh --with-background-agent --max-subagents 6
```

`--with-all` is an alias that enables every optional package (currently only the background agent).

The install script writes:

- `services/background-agent/background-agent.env`

Start:

```bash
./services/background-agent/scripts/run.sh
```

`--max-subagents N` configures both `CODEX_API_MAX_SUBAGENTS` and `BACKGROUND_MAX_SUBAGENTS`. The current project default is `6`.

## Legacy Local-Model Services

The previous local inference stack (`services/webinfer`, `services/asr`, `services/tts`) is no
longer installed or started by the scripts in this directory. The source remains in the
repository for reference; see each service's own README if you ever need to run it manually.
