#!/bin/bash
# 启动 laya-service
set -euo pipefail
cd "$(dirname "$0")"

# 国内环境拉权重卡住时，取消注释下面两行走镜像
# export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

# ---- 推荐配置（按本机 M5 / 16GB 实测定：服务占用 5.3GB，满预算 8192 单条 1.7s）----
export LAYA_DEVICE=mps                    # fp16 半精度
export LAYA_MODELS=english,multilingual   # 16GB 勿加 typed-decisions（再 +1.5~2GB）
export LAYA_MAX_LOADED=2                  # 钉死常驻分支，防误配 3 分支触发驱逐/换页
export LAYA_PRELOAD=1                     # 启动即预加载，内存装得下
export LAYA_MAX_CONCURRENT=8              # 推理串行，排队纯等待；满 8 快速 503 优于长队列
export LAYA_MAX_TOKEN_BUDGET=4096         # worst-case 从 1.7s 压到 ~0.4s；需长文档判定时再调回 8192
# LAYA_API_KEY 留空：仅 127.0.0.1 绑定；改 0.0.0.0 时必须设置

exec uv run uvicorn server:app \
  --host "${LAYA_HOST:-127.0.0.1}" \
  --port "${LAYA_PORT:-8399}"
