# Container 部署

> 原文档: [README.md](./README.md)

同一份 Docker Compose 配置运行剩下的两个服务：WebUI（浏览器前端 + 阿里云百炼
Qwen-Omni-Realtime 云端 API 桥接）和后台 agent（Codex CLI 的 FastAPI 封装）。推理在
云端运行，因此不需要 GPU、模型权重或 vLLM 镜像。

历史遗留的 `regular` / `24GB` / `16GB` GPU 规格仅为兼容命令行而保留，三者现在完全
相同——任选其一都会启动同样的服务栈。启动任一规格时会自动关闭另外两个。

## 环境要求

- 装有 Docker Engine 和 Docker Compose 的 Linux。
- 空闲主机端口：`8079` 和 `8099`。
- 阿里云百炼（DashScope）API Key，用于 Qwen-Omni-Realtime API。

## 快速开始

从仓库根目录执行：

```bash
# 1. 拉取基础镜像并构建服务镜像。
docker pull python:3.12-slim-bookworm
docker pull node:22-bookworm-slim
./container/scripts/build-images.sh

# 2. 创建并编辑一个规格配置（设置 DASHSCOPE_API_KEY）。
cp container/regular/.env.example container/regular/.env
${EDITOR:-vi} container/regular/.env

# 3. 启动并验证所有端点。
./container/manage.sh regular up
./container/manage.sh regular test
```

打开 `https://<host>:8099`。WebUI 使用自动生成的自签名证书。

## 常用操作

```bash
./container/manage.sh regular status
./container/manage.sh regular logs
./container/manage.sh regular restart
./container/manage.sh regular down
```

可将 `regular` 换成 `24GB` 或 `16GB`——三个规格完全等价。

## 配置

- `DASHSCOPE_API_KEY`：百炼（DashScope）API Key；必填。
- `OMNI_REALTIME_URL`：Omni realtime WebSocket 端点；内置默认值。
- `OMNI_MODEL`：realtime 模型名；默认 `qwen3.5-omni-flash-realtime`。
- `WEBUI_PORT`：WebUI 监听端口；默认 `8099`。
- `BACKGROUND_AGENT_PORT`：background-agent 监听端口；默认 `8079`。
- `CODEX_HOME_HOST`：Codex 配置来源；默认为 `../services/background-agent/codex-home`。
- `BACKGROUND_WORKSPACE_HOST`：可写的 background-agent 工作区。

相对路径基于 `container/docker-compose.yml` 解析。`.env` 文件仅保留在本地，
切勿提交 API Key 或 Codex 凭据。精确的镜像版本记录在 `images.lock` 中。
