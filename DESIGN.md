# DESIGN.md — 设计备忘（已调研、未实装）

> 记录已完成源码调研与实测验证、但暂未实装的设计方案。将来采用时按本文档直接实施，无需重新调研。
>
> 依据：laya 0.3.22 源码（`agent.py:1283-1497`、`router.py:180-208, 823-879`、`serve.py`）+ 本机实测。
> 实测环境：Apple M5 / 16GB 统一内存 / 服务进程物理占用 5.3GB（english + multilingual 双分支常驻）。
> 最后更新：2026-09-30

---

## 一、predict_long 路由：POST /v1/systemone/long（长文档判定）

**状态：未实装。** 当前 `serve.py` 仅注册 `/health`、`/v1/systemone`、`/v1/systemone/batch`；`/v1/systemone` 对超窗 state 直接单窗截断（list 左截断保留最新 / str、dict 右截断保留开头），超出部分静默丢弃。

### 1.1 核心事实（调研结论）

- **`Router` 自带 `predict_long`**（`router.py:823`），实现为一个 start hook（`_ScanLong`，`router.py:180`）：挂在 `Router.predict` 的路由流程上，路由完成后改调该分支 Agent 的 `predict_long` 扫描全部窗口。**与 `/v1/systemone` 同一条代码路径**——同样的路由、hooks、串行化、`routing`/`usage` 返回结构。
- 它**不接受 `max_len` / `head_max_len`**——绕开单窗截断正是它的存在意义；窗口大小由 `window` 参数或分支 checkpoint 预算决定。
- 窗口机制（`agent.py:1283-1497`）：
  - 默认 `window = max(64, max_len − head_max_len − 8)`：english **312** / multilingual **760** / typed-decisions **760** token；`stride` 默认 `window // 2`（50% 重叠，保证 ≤ 半窗长度的关键 span 完整落进某个窗口）
  - tokenize 一次 → 切窗 → `predict_batch` 一次批量前向 → 按题型聚合：
    - `noul`：P(true) 取各窗**最大值**（任一窗口支持即成立）
    - `choice` / `score`：取**最自信窗口**的答案（避免大量中性窗口平均后淹没局部信号）
  - state 装得下单窗时直通 `system_one`（零开销），`usage["windows"]` 恒存在：1=单窗直通，N=扫描窗数，0=start hook 抢答
  - 窗口间**无 session 关联**：靠同一次调用内的列表下标 + `predict_batch` 保序对应，`answer["window"] = {index, token_start, token_end, count}` 只是响应里的临时归因指针，服务端零持久化
- **与 `LAYA_MAX_TOKEN_BUDGET` 正交**：该环境变量只校验调用方传的 `max_len`（单次前向的序列上限）；predict_long 的窗口按分支配置内部生成，每次前向都合规。总量上限仅受 HTTP 层 `state ≤ 50000 字符` 约束。

### 1.2 Wire 契约提案

```jsonc
// POST /v1/systemone/long
{
  "state": { "document": "..." },        // 同现有三种形态；长文档建议 str/dict
  "questions": { /* 同 /v1/systemone */ },
  "window": 760,                          // 可选；建议校验 ≤ LAYA_MAX_TOKEN_BUDGET − 8，超出 422
  "stride": 380,                          // 可选；默认 window // 2
  "batch_size": 16,                       // 可选；单次前向的窗口数上限（约束 MPS 显存峰值）
  "model": "multilingual"                 // 可选；省略则照常语种分流
}
// 响应：与 /v1/systemone 完全同形，附加 usage["windows"]；answers.<题>.window 归因决定窗口
```

### 1.3 实装要点（server.py 装配层，预计 <40 行）

```python
@app.post("/v1/systemone/long")
async def systemone_long(request: Request):
    body = await request.json()
    # 复用 /v1/systemone 的全部校验（_check_request_limits / 可选鉴权 / min_confidence）
    window = int(body.get("window") or 0) or None
    if window and window > TOKEN_BUDGET - 8:
        raise HTTPException(422, f"window exceeds server limit ({window} > {TOKEN_BUDGET - 8})")
    result = await run_in_threadpool(
        lambda: router.predict_long(
            body["state"], questions, model=model,
            window=window, stride=body.get("stride"), batch_size=body.get("batch_size"),
            **predict_kwargs))          # lang / lang_guess / min_confidence 照现有透传
    return JSONResponse(result, headers={"Server-Timing": f"inference;dur={dur}"})
```

- 推理照旧走 laya 的 ThreadPoolExecutor(1) + asyncio.Lock，装配层勿加锁（与现有纪律一致）
- `router.predict_long` 内部路由语种，无需手工挑分支

### 1.4 代价与警告（laya 文档串明确 + 实测）

1. **延迟线性**：N 窗 = N 次前向（批量摊薄、但共享全局串行队列）。实测锚点：1024-token 序列 ~100ms / 满 8192 单条 ~1.7s。50000 字符中文 ≈ 2.5–3.5 万 token ≈ 65–90 窗 → **秒级到十秒级**，期间阻塞后续所有请求。
   - 缓解：long 路由单独限并发（信号量 1–2，满时 503 + Retry-After，不与普通请求抢闸门）；或文档建议调用方拆段降频
2. **概率非文档级校准**：返回的是「决定窗口」的概率——`noul` 的 max 随窗数虚高（无信号也漂高）；`choice` 可能落在自信的中性窗上。**调用方必须读 `answer.window` 归因复核原始 span，勿把 raw 数当全局置信度。**
3. **usage 语义变化**：`truncated` 从布尔变为「被切的窗口数」（跨窗求和），判据用 `> 0` 而非 `is True`；`state_tokens` / `state_tokens_dropped` 同样跨窗求和（含重叠）。

### 1.5 启用条件

出现真实的「整篇文档护栏 / 长文路由」调用方且可接受秒级延迟时再实装；当前无此需求，保持服务最小。

---

## 二、长文档评分复用（可选扩展，「session 的地盘」）

**状态：仅设计。** 动机：predict_long 每次全量重算——同一份长文档换 questions 重判，65–90 窗前向全部重跑。

### 2.1 三种形态（按需递进）

| 形态 | 收益 | 需要的键 |
|---|---|---|
| 窗口评分缓存 | 同文档换题复用前向（只重聚合，零前向） | doc_id + 窗口哈希 |
| 增量扫描 | 文档追加后只对新增窗口前向 | doc_id + 已扫 offset |
| 断点续跑 | 超时/崩溃后恢复扫描进度 | doc_id + 进度 |

### 2.2 缓存契约（引入复用即必须引入 document ID）

```
cache_key = sha256(model_revision + window_cfg + window_token_ids)
value    = {qid: 各窗原始 logits / answers}      # 缓存原始评分，不缓存聚合结果
```

- **缓存粒度是窗口×题目的原始评分**，不是最终答案：换题时只重跑聚合（纯 Python，零前向）；归因 `index/token_start/end` 不受影响
- **失效条件**：model revision 变化（`/health` 的 `revisions` 已暴露，可直接用）、窗口配置变化、TTL 过期
- 存储：进程内 LRU（如 ≤512 窗 × 每窗几 KB）+ TTL；不上外部存储

### 2.3 职责边界（关键决策）

**放在 laya-service 装配层或调用方，不改 laya 包。** laya 的核心卖点是判别式、无状态、单次前向、可水平复制——加会话存储会破坏该模型。单实例进程内 LRU 足够；将来若多实例部署，缓存下沉到调用方自己的存储层。

> 语义澄清：predict_long 的窗口不是 session——窗口间靠「位置对应」关联（见 1.1），无需 ID；只有**跨请求复用**（本节）才需要 document ID。

---

## 三、支撑实测数据（2026-09-30，Apple M5 / 16GB）

| 场景 | 数值 |
|---|---|
| 服务进程物理占用（2 分支常驻） | 5.3 GB |
| 短对话（multilingual 默认额度 ≈991 token） | ~17 ms |
| 1024-token 序列 | ~100 ms |
| 满预算 8192 单条 | ~1.7 s |
| multilingual 默认 state 容量 | ≈991 token ≈ 20 轮短对话；40 轮（1151 token）即截断 |
| state 列表形态 | 无条数/每条限制，压平按总 token 预算截断（token 粒度，可切开单条） |

当前生效配置（`start.sh`）：`LAYA_MAX_TOKEN_BUDGET=4096`、`LAYA_MAX_CONCURRENT=8`、`LAYA_MAX_LOADED=2`。实装 long 路由时注意它与 `window ≤ 4088` 校验的联动。
