# JoyVL 安装说明

> 原文档: [README.md](./README.md)

推理现已迁移至阿里云百炼 Qwen-Omni-Realtime 云端 API。全新安装只会部署剩下的两个运行时服务——WebUI（浏览器前端 + 云端 API 桥接）和后台 agent（Codex CLI 的 FastAPI 封装）。本地不再安装 vLLM、PyTorch、模型权重或音频运行时。

除非另有说明，请从仓库根目录运行下面的命令。

## 核心安装

- `install.sh` 使用 `uv venv` 创建虚拟环境，再用 `uv pip install` 安装依赖。
- `install.sh` 以可编辑模式安装 WebUI（aiohttp、aiortc、websockets、Pillow 等）。
- 本安装目录统一使用 Python 3.12。

```bash
./install/install.sh
```

## Omni Realtime API 配置

WebUI 通过环境变量连接百炼 Omni realtime API：

| 变量 | 是否必填 | 说明 |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | 是 | 百炼（DashScope）API Key |
| `OMNI_REALTIME_URL` | 否 | Omni realtime WebSocket 端点（内置默认值） |
| `OMNI_MODEL` | 否 | realtime 模型名（默认 `qwen3.5-omni-flash-realtime`） |

```bash
export DASHSCOPE_API_KEY=sk-...
```

## 后台 Agent

安装：

```bash
./install/install.sh --with-background-agent --max-subagents 6
```

`--with-all` 是启用全部可选包（当前仅后台 agent）的别名。

安装脚本会写入：

- `services/background-agent/background-agent.env`

启动：

```bash
./services/background-agent/scripts/run.sh
```

`--max-subagents N` 同时配置 `CODEX_API_MAX_SUBAGENTS` 和 `BACKGROUND_MAX_SUBAGENTS`。当前项目默认值为 `6`。

## 旧版本地模型服务

此前的本地推理栈（`services/webinfer`、`services/asr`、`services/tts`）不再由本目录的脚本安装或启动。源码仍保留在仓库中仅供参考；如确需手动运行，请参见各服务自带的 README。
