# 24GB Profile

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Complete the prerequisites and image build in `../README.md` first.

The historical GPU profiles are kept for CLI compatibility, but all profiles now start the
same two services — WebUI (`:8099`) and background-agent (`:8079`) — with inference on
the Bailian Qwen-Omni-Realtime cloud API. No local GPU is required.

```bash
cp container/24GB/.env.example container/24GB/.env
# edit .env and set DASHSCOPE_API_KEY
./container/manage.sh 24GB up
```

Edit `.env` to change the Omni API configuration or service ports.
