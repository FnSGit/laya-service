# laya-service

Laya 判别式决策模型的本地 HTTP 服务，供其他 agent 调用。

- 模型：[convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya)（Apache-2.0，TypeSafe Jev 开源复刻）
- 特点：意图识别 / 路由分流 / 护栏判定，单次前向毫秒级，输出结构化概率，不做文本生成
- 协议：**TypeSafe Jev `/v1/systemone` wire 协议**，现成 Jev 客户端（如 hs-jev）把 `baseUrl` 指到本服务即可用
- 设备：Apple Silicon MPS（fp16），权重常驻统一内存

## 启动

```bash
./start.sh
# 默认 127.0.0.1:8399，首次启动会下载 english 分支权重（multilingual 已缓存）
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LAYA_PORT` | 8399 | 监听端口（由 start.sh 传给 uvicorn） |
| `LAYA_HOST` | 127.0.0.1 | 监听地址；供局域网访问时改 0.0.0.0 |
| `LAYA_DEVICE` | mps | 推理设备 |
| `LAYA_MODELS` | english,multilingual | 预加载分支，可加 `typed-decisions` |
| `LAYA_API_KEY` | 空 | 设置后判定接口需带 `Authorization: Bearer <key>` |
| `LAYA_MAX_LOADED` | Router 默认（2） | 常驻分支数上限（预加载的不受影响） |
| `LAYA_PRELOAD` | 1 | 启动即预加载；置 0 则首次请求时才加载 |

## 接口（TypeSafe Jev wire 协议）

由 laya 包官方的 `laya.serve` 实现，含请求限流与全部输入防护。

### GET /health

```bash
curl -s http://127.0.0.1:8399/health | jq
# {"status":"ok","loaded":["english","multilingual"],"device":"mps",...}
```

### POST /v1/systemone（单条判定）

```bash
curl -s -X POST http://127.0.0.1:8399/v1/systemone \
  -H 'Content-Type: application/json' \
  -d '{
    "state": {
      "from": "user@acme.com",
      "subject": "发票重复扣款",
      "body": "三月被扣了两次款，请今天退款，否则取消订阅。"
    },
    "questions": {
      "department": {
        "type": "choice",
        "instructions": "该请求应由哪个部门处理？",
        "criteria": {
          "billing": "发票、支付、退款",
          "technical": "bug、故障、系统错误",
          "sales": "报价、新合同",
          "other": "其他"
        }
      }
    }
  }' | jq '.answers.department'
```

返回（节选）——顶层为 `{model, answers, usage, routing}`：

```json
{
  "choice": "billing",
  "probabilities": { "billing": 1.0, "technical": 0.0, "...": "..." },
  "confidence": 0.9998,
  "answer_confidence": 1.0
}
```

- `state`：任意 JSON（文本或结构化字段），是模型要判定的输入
- `questions`：题目字典，`type` 支持 `choice`（单选）/ `score`（打分，criteria 为级别描述列表）/ `noul`（布尔；中文场景建议改双选项 choice）
- `model`：可选，指定分支（`english` / `multilingual` / `typed-decisions`）；未知值（如 Jev 客户端传的 `jev-1`）自动视为不指定，按语种分流
- 其余可选控制字段：`max_len` / `head_max_len`（token 预算）、`lang` / `lang_guess`、`min_confidence`（低置信返回 abstain 类结果）
- `routing.model` 是实际使用的分支；中文为主自动走 multilingual

### POST /v1/systemone/batch（批量）

```json
{ "states": [ /* 最多 64 个 state */ ], "questions": { /* 同上 */ } }
```

返回 `{ "results": [...], "total_usage": { "input_tokens": N, ... } }`。

### 上下文与 token 预算

`state` 支持三种形态：文本字符串、JSON 对象、**对话轮次列表**（list）。服务端无状态、无会话概念——多轮上下文完全由调用方每次请求重新拼进 `state`，服务不保存历史。

- **无条数/每条限制**：列表会被 `json.dumps` 压平成一个字符串整体参与判定，不存在「最多多少轮」「每轮多少 token」校验（仅 `state` ≤ 50000 字符）
- **没有角色概念**：tokenizer 无任何 role 特殊 token（只有 `[CLS]/[SEP]/[MASK]/[PAD]/[UNK]`），`role`/`content` 只是模型微调时学到的普通 JSON 字段；序列格式为 `[CLS] 题干 [SEP] [MASK]选项… [SEP] state [SEP]`，**state 排在最末尾**（题干+选项优先占预算）
- **截断方向按形态区分**：列表（对话）→ 左截断保留最新内容；字符串/对象（文档）→ 右截断保留开头
- **截断是 token 粒度，轮次边界不参与**：超预算时一条消息可能被从中间切开——轮次裁剪/摘要压缩需调用方自己做
- **可观测**：`usage` 含 `state_tokens`（全量）/ `state_tokens_dropped` / `truncated` / `truncated_questions`；实际用量 = `state_tokens − state_tokens_dropped`

token 预算分层：

| 层级 | 值 | 说明 |
|---|---|---|
| 模型硬上限 | 8192 | ModernBERT/mmBERT `max_position_embeddings` |
| 服务准入 | 8192 | `LAYA_MAX_TOKEN_BUDGET`，`max_len` 超出报 422 |
| 分支默认 `max_len` | 512 / 1024 / 1024 | english / multilingual / typed-decisions |
| 分支 `head_max_len` | 192 / 256 / 256 | 题干+选项占用 |
| state 实际可用 | ≈ `max_len − head_max_len − 1` | multilingual 默认约 991 token |

容量参考（multilingual 默认额度 ≈ 991 token）：20 轮短对话（571 token）不截断，40 轮（1151 token）即丢 160 token。中文请求默认走 multilingual，**需要更长上下文必须显式传 `max_len`**（最高 8192）。

> laya 包另有 `predict_long`（超长 state 滑窗扫描+按题型聚合，choice 取最自信窗口），未暴露到 HTTP——长 state 超出 `max_len` 的部分会被直接丢弃。

### 限制与错误码

| 码 | 场景 |
|---|---|
| 400 | body 不是对象或缺 `questions` |
| 401 | `LAYA_API_KEY` 已设置但未携带/带错 Bearer Token |
| 413 | 单题 choice 选项超过 100 |
| 422 | 题目畸形、选项放不下 token 预算、score 级别缺描述 |
| 503 | 模型忙（并发满），响应带 `Retry-After: 1` |

其余限制：`state` ≤ 50000 字符、≤ 64 题、body ≤ 2MB；成功响应带 `Server-Timing: inference;dur=...` 头。

## 常驻（launchd）

已配置 LaunchAgent：登录自启 + 崩溃自动拉起（KeepAlive）。

```bash
# 加载（首次安装后执行一次）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ai-models.laya-service.plist

# 卸载
launchctl bootout gui/$(id -u)/com.ai-models.laya-service

# 重启服务
launchctl kickstart -k gui/$(id -u)/com.ai-models.laya-service

# 查看状态
launchctl print gui/$(id -u)/com.ai-models.laya-service | rg state
```

日志：`/tmp/laya-service.log`（stdout）+ `/tmp/laya-service.err.log`（stderr）。
plist 副本在本仓库内（com.ai-models.laya-service.plist），换机时可拷贝到 `~/Library/LaunchAgents/` 后执行加载命令。

## 注意

- **v0.1 的自定义 `/predict` 端点已移除**，统一走 Jev 协议 `/v1/systemone`
- 权重在启动时预加载，端口在就绪后才监听（约 10s）；首次前向仍有约 1.6s 的 MPS 预热，服务启动后建议先空跑一条
- `state`/`questions` 会参与语言检测：中文为主自动走 multilingual 分支
- 门控建议读 `answer_confidence`（所报答案的校准概率）；`confidence` 是 1−归一化熵（分布集中度），语义与 Jev 的 confidence 不同，Jev 阈值不能直接搬。中文场景自动放行阈值建议 ≥ 0.95，最好按自己的数据重校
