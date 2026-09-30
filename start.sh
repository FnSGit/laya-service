#!/bin/bash
# 启动 laya-service
set -euo pipefail
cd "$(dirname "$0")"

# 国内环境拉权重卡住时，取消注释下面两行走镜像
# export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

exec uv run uvicorn server:app \
  --host "${LAYA_HOST:-127.0.0.1}" \
  --port "${LAYA_PORT:-8399}"
