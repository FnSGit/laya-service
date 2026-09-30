"""Laya 决策模型本地 HTTP 服务 —— TypeSafe Jev `/v1/systemone` wire 协议。

直接复用 laya 包官方的 laya.serve 实现：Jev 协议端点、Bearer 鉴权、
推理串行化、请求限流与防护（413/422/400/503）全部由官方代码承担，
本文件只做启动装配与默认值。

端点：
    GET  /health                存活与分支状态（status/loaded/device/...）
    POST /v1/systemone          单条判定：{state, questions, model?}，Jev 客户端直接可用
    POST /v1/systemone/batch    批量判定：{states, questions, model?}，最多 64 个 state

环境变量（与 laya.serve 原生约定一致）：
    LAYA_PORT / LAYA_HOST      由 start.sh 传给 uvicorn（默认 8399 / 127.0.0.1）
    LAYA_DEVICE                推理设备（默认 mps，Apple Silicon GPU）
    LAYA_MODELS                预加载分支，逗号分隔（默认 english,multilingual）
    LAYA_API_KEY               可选 Bearer Token，设置后判定接口需携带
    LAYA_MAX_LOADED            常驻分支数上限（未设用 Router 默认 2）
    LAYA_PRELOAD               启动即预加载权重（默认开；端口在就绪后才监听）
"""

import os

from laya.serve import create_app

# 与旧版 laya-service 保持一致的默认行为：MPS 优先、只预加载这两个分支
os.environ.setdefault("LAYA_DEVICE", "mps")
os.environ.setdefault("LAYA_MODELS", "english,multilingual")

app = create_app()
