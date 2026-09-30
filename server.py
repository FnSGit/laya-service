"""Laya 决策模型本地 HTTP 服务。

将 Laya（判别式决策模型，Jev 开源复刻版）封装为 HTTP 接口，
供本机 / 局域网内的其他 agent 调用。

环境变量：
    LAYA_PORT       监听端口（默认 8399）
    LAYA_HOST       监听地址（默认 127.0.0.1，仅本机）
    LAYA_DEVICE     推理设备 mps / cpu（默认 mps，Apple Silicon GPU）
    LAYA_MODELS     加载的分支，逗号分隔（默认 english,multilingual）
    LAYA_API_KEY    可选 Bearer Token，设置后所有请求需携带
"""

import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from laya import Router

REPO = "convaiinnovations/laya"
BRANCHES: Dict[str, Optional[str]] = {
    "english": None,
    "multilingual": "multilingual",
    "typed-decisions": "typed-decisions",
}

DEVICE = os.environ.get("LAYA_DEVICE", "mps")
MODELS_ENV = os.environ.get("LAYA_MODELS", "english,multilingual")
API_KEY = os.environ.get("LAYA_API_KEY", "")
MAX_LOADED = int(os.environ.get("LAYA_MAX_LOADED", "2"))

state: Dict[str, Any] = {"router": None, "device": DEVICE, "branches": []}
_predict_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    names = [m.strip() for m in MODELS_ENV.split(",") if m.strip()]
    unknown = [n for n in names if n not in BRANCHES]
    if unknown:
        raise RuntimeError(f"未知分支: {unknown}，可选: {list(BRANCHES)}")
    models = {n: (REPO, BRANCHES[n]) for n in names}
    print(f"[laya-service] 加载分支 {names}，device={DEVICE} ...", flush=True)
    router = Router(models=models, device=DEVICE, preload=True, max_loaded=MAX_LOADED)
    state["router"] = router
    state["branches"] = names
    print("[laya-service] 就绪", flush=True)
    yield
    state["router"] = None


app = FastAPI(title="laya-service", version="0.1.0", lifespan=lifespan)


def check_auth(authorization: str = Header(default="")) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


class PredictRequest(BaseModel):
    """state: 待判定文本/结构；questions: 题目定义；model: 可选指定分支。"""

    state: Any
    questions: Dict[str, Any]
    model: Optional[str] = None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok" if state["router"] is not None else "loading",
        "device": state["device"],
        "branches": state["branches"],
    }


@app.post("/predict", dependencies=[Depends(check_auth)])
def predict(req: PredictRequest) -> Dict[str, Any]:
    router: Router = state["router"]
    if router is None:
        raise HTTPException(status_code=503, detail="model still loading")
    # 底层模型非线程安全，串行化推理；单次前向仅几十毫秒，锁开销可忽略
    with _predict_lock:
        try:
            return router.predict(req.state, req.questions, model=req.model)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
