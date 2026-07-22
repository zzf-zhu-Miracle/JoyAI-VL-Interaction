# 入门指南

> 原文档: [getting_started.md](./getting_started.md)

## 前置条件

- Linux（无需本地 GPU —— 推理走百炼云端 API）
- Python 3.12（推荐）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip，用于 Python 包管理
- 阿里云百炼（DashScope）API Key，用于 Qwen-Omni-Realtime API

## 安装

使用提供的安装脚本设置所有依赖：

```bash
# 安装 WebUI + 后台 agent 依赖
./install/install.sh --with-all
```

跨平台兼容性说明请参阅 `install/README.md`。

`install/` 只用于依赖设置和生成配置。运行时入口位于 `services/`：使用 `services/scripts/run.sh` 进行编排，或使用 `services/<service>/scripts/` 下的脚本进行组件级操作。

## Omni Realtime API 配置

推理通过阿里云百炼 Qwen-Omni-Realtime 云端 API 运行。启动 WebUI 前通过环境变量配置：

```bash
export DASHSCOPE_API_KEY=sk-...   # 必填
# 可选覆盖项（内置默认值）：
# export OMNI_REALTIME_URL=wss://...
# export OMNI_MODEL=qwen3.5-omni-flash-realtime
```

## 最小部署（仅 WebUI）

只启动 Web UI：

```bash
./services/scripts/run.sh minimal
```

`services/scripts/run.sh minimal` 会让 WebUI 保持在前台运行。在该终端按 `Ctrl+C` 可停止由编排器启动的服务。

在浏览器中打开：`https://127.0.0.1:8099`

## 完整部署（所有服务）

后台 agent 必须在 **Web UI 之前**启动。

### 推荐启动顺序

```text
1. background-agent           （可选，启用任务委托）
2. services/webui             （必需，最后启动）
```

### 分步说明

如果保留可选的 `background-agent`，请先准备 `CODEX_HOME`，并建议提前阅读
[background-agent README](../services/background-agent/README.zh-CN.md)。你可以把
`CODEX_HOME` 指向已有 Codex home，也可以把 `auth.json` 和 `config.toml` 复制到
默认服务路径：

```bash
# 方式 1：让 background-agent 使用已有的 Codex home
export CODEX_HOME=/path/to/your/codex-home

# 方式 2：使用默认的服务内 Codex home
mkdir -p services/background-agent/codex-home
cp /path/to/your/codex-home/{auth.json,config.toml} services/background-agent/codex-home/
```

一条命令启动完整服务集：

```bash
./services/scripts/run.sh all
```

`services/scripts/run.sh all` 会在 WebUI 之前启动可选服务。可使用 `START_BACKGROUND_AGENT=0` 跳过后台 agent。启用 `background-agent` 时，它的 `CODEX_HOME` 必须包含 `config.toml` 和 `auth.json`。使用该编排器时，只有最终的 WebUI 进程启动后，完整服务集才算就绪。在该终端按 `Ctrl+C` 可停止由编排器启动的服务。

也可以按下面顺序手动启动服务：

```bash
# 1. 后台 Agent（可选）
./services/background-agent/scripts/run.sh

# 2. Web UI（最后启动）
source services/.venv/bin/activate
(cd services/webui && bash scripts/start_server.sh)
```

## 健康检查

启动后，确认各服务正在运行：

```bash
curl http://127.0.0.1:8079/health   # background-agent（可选）
```

Web UI 可通过 `https://127.0.0.1:8099` 访问（接受自签名证书警告）。

## RTSP 本地推流测试

WebUI 可以使用摄像头，也可以使用 RTSP 输入。如果没有真实 IP 摄像头，可以在本机运行 MediaMTX 服务，并用 `ffmpeg` 把本地视频文件推成 RTSP 流。

本地流启动后，在 WebUI 的 RTSP 输入框填写类似 `rtsp://127.0.0.1:8554/fire1` 的地址。如果 WebUI 运行在另一台机器上，请把 `127.0.0.1` 换成运行 MediaMTX 的机器地址。

MediaMTX 下载说明、辅助脚本示例和常见检查请参阅 [RTSP 本地推流说明](rtsp_streaming.zh-CN.md)。

## 停止服务

如果通过 `services/scripts/run.sh minimal` 或 `services/scripts/run.sh all` 启动，请在该终端按 `Ctrl+C`。如需从另一个终端停止服务：

```bash
./services/scripts/stop.sh all
```

## 配置

### Omni realtime API（WebUI）

关键环境变量（启动 WebUI 前导出）：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `DASHSCOPE_API_KEY` | — | 百炼（DashScope）API Key；必填 |
| `OMNI_REALTIME_URL` | 内置百炼 realtime 端点 | Omni realtime WebSocket 端点 |
| `OMNI_MODEL` | `qwen3.5-omni-flash-realtime` | realtime 模型名 |
| `OMNI_VOICE` | `Ethan` | Omni 会话使用的 TTS 音色 |
| `VENV_ACTIVATE` | 自动检测 `services/.venv` | 可选虚拟环境 activate 脚本路径；设置 `VENV_ACTIVATE=` 可使用当前 shell 环境 |

### background-agent

background-agent 会封装本地 `codex` CLI，用于委托后台任务。它的默认运行配置位于
`services/background-agent`：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `CODEX_HOME` | `services/background-agent/codex-home` | Codex home，需包含 `config.toml` 和 `auth.json`；可以指向已有 Codex home，或把这两个文件复制到默认路径 |
| `CODEX_API_WORKSPACE` | `<repo>/agent-workspace` | background Codex 运行使用的工作区；启动时自动创建 |
| `CODEX_API_HOST` | `127.0.0.1` | background-agent 绑定地址 |
| `CODEX_API_PORT` | `8079` | background-agent 监听端口 |
| `CODEX_API_MAX_SUBAGENTS` | `6` | API wrapper 使用的最大并行 subagent 数量 |
| `BACKGROUND_AGENT_API_URL` | `http://127.0.0.1:8079` | WebUI 访问 background-agent 使用的 URL |

默认 `CODEX_HOME` 使用服务内路径，方便 background-agent 独立于当前 shell 运行。
首次配置时，请在启动前执行 `export CODEX_HOME=/path/to/your/codex-home`，
或把 `auth.json` 和 `config.toml` 复制到 `services/background-agent/codex-home/`。

## 故障排查

常见启动和运行问题请参阅 [故障排查指南](troubleshooting.zh-CN.md)。其中包含 Omni API 连接、
本地图片路径，以及 `background-agent` 的 `CODEX_HOME` 配置问题。
