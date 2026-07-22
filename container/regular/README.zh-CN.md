# regular 规格

> 原文档: [README.md](./README.md)

请先完成 `../README.zh-CN.md` 中的前置准备和镜像构建。

历史遗留的 GPU 规格仅为兼容命令行而保留；所有规格现在都启动相同的两个服务——
WebUI（`:8099`）和 background-agent（`:8079`），推理走百炼 Qwen-Omni-Realtime
云端 API，无需本地 GPU。

```bash
cp container/regular/.env.example container/regular/.env
# 编辑 .env 并设置 DASHSCOPE_API_KEY
./container/manage.sh regular up
```

编辑 `.env` 可修改 Omni API 配置或服务端口。
