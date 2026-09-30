# laya-service

Laya 判别式决策模型的本地 HTTP 服务，供其他 agent 调用。

- 模型：[convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya)（Apache-2.0，TypeSafe Jev 开源复刻）
- 特点：意图识别 / 路由分流 / 护栏判定，单次前向毫秒级，输出结构化概率，不做文本生成
- 设备：Apple Silicon MPS（fp16），权重常驻统一内存

## 启动

```bash
./start.sh
# 默认 127.0.0.1:8399，首次启动会下载 english 分支权重（multilingual 已缓存）
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LAYA_PORT` | 8399 | 监听端口 |
| `LAYA_HOST` | 127.0.0.1 | 监听地址；供局域网访问时改 0.0.0.0 |
| `LAYA_DEVICE` | mps | 推理设备 |
| `LAYA_MODELS` | english,multilingual | 加载分支，可加 `typed-decisions` |
| `LAYA_API_KEY` | 空 | 设置后请求需带 `Authorization: Bearer <key>` |

## 接口

### GET /health

```bash
curl -s http://127.0.0.1:8399/health | jq
# {"status":"ok","device":"mps","branches":["english","multilingual"]}
```

### POST /predict

```bash
curl -s -X POST http://127.0.0.1:8399/predict \
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

返回（节选）：

```json
{
  "choice": "billing",
  "probabilities": { "billing": 0.99, "technical": 0.004, "...": "..." },
  "confidence": 0.99
}
```

- `state`：任意 JSON（文本或结构化字段），是模型要判定的输入
- `questions`：题目字典，`type` 支持 `choice`（单选）/ `score`（打分）/ `boolean`（中文建议改双选项 choice）
- `model`：可选，强制指定分支（`english` / `multilingual` / `typed-decisions`），一般不传，自动按语种分流
- 门控读 `confidence`，不要读 `action.act_probability`（恒为 1.0）
- 中文场景置信度偏高，自动放行阈值建议 ≥ 0.95

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

- 首次前向约 1.6s（MPS/CUDA 初始化预热），服务启动后建议先空跑一条
- `state`/`questions` 会参与语言检测：中文为主自动走 multilingual 分支
