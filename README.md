# idea-to-pr

> 往 Telegram 丢一个想法（打字或语音），agent 把它变成规格说明、开 GitHub Issue、搭脚手架，最后递回来一个 draft PR。

[![CI](https://github.com/EasonFang/idea-to-pr/actions/workflows/ci.yml/badge.svg)](https://github.com/EasonFang/idea-to-pr/actions/workflows/ci.yml)

## 核心原则

**在"聊确认 scoped spec"之前，不委派任何任务。**

模糊 prompt 直接丢给 agent 是本项目最大的失败模式。确认步骤是护栏，不可绕过：
想法进来先解析成规格 → 发给本人确认 → 确认后才开 Issue、才交给 Devin。

## 架构

```
Telegram ──webhook──▶ [ingress] ──enqueue──▶ [worker] ──▶ GitHub Issues
                      FastAPI         │                        │
                   HMAC 校验        InProcess              按标题 hash 去重
                   快速 ack (<100ms) Queue                     │
                        │                                     ▼
                   "收到…" 回复                      Devin Sessions API
                   （尽力而为）                      复现→测试→CI→draft PR
                        │                                     │
                        └──────── 进度事件流 ◀────────────────┘
```

**文本与语音共用一条队列**：语音经 file_id 下载后本地 faster-whisper 转写，
worker 拿到的永远是纯文本。不做两条并行流水线。

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 入口 | Python + FastAPI | Telegram SDK 生态成熟 |
| 队列 | 进程内同步队列（M1） | 契约极简，后续可换 Celery/Redis 或 HTTP-to-TS |
| 转写 | faster-whisper（本地） | 免费、隐私、无按分钟计费 |
| 结构化 | 廉价快模型（可注入） | 额度花在写代码，不花在胶水 |
| 执行 | Devin Sessions API | 人离开环的委托，消除"我"这个瓶颈 |
| 状态 | SQLite | 单人够用，零运维 |

## 失败模式与对策

- **Devin 卡死（最高成本风险）**：四道防线——3 次重试硬上限、scaffold prompt 钉死 lockfile、
  同一文件+同一错误签名两次判定死循环、`/stop` 逃生舱。卡死的 session 成本 = N 次尝试 + 一条通知。
- **webhook 重试风暴**：`sendMessage` 失败绝不让 webhook 返回 5xx，异常吞掉，照样 200。
- **机器人被屏蔽**：贴纸/频道帖/编辑/空消息 ack 后静默忽略，不发任何回复。
- **伪造请求**：配了 secret 却没签名头 → 直接 401（fail closed），常数时间比较防时序侧信道。

## 完成流程（Devin 结束时到底发生什么）

这是从"Devin 干完活"到"Telegram 里一键决策"的完整链路，全部为事件驱动，不靠轮询：

```
Devin session 状态变化
        │  POST /devin-webhook  (X-Devin-Webhook-Secret 校验，fail closed)
        ▼
normalise_event()          统一 v1/v2/v3 多种 payload 形状
        │
        ▼
SessionRegistry            用 session_id 反查发起它的 chat_id（webhook 本身不带 chat）
   + seen_events 去重        Devin at-least-once 投递不会造成重复消息
        │
        ▼
sendMessage + inline_keyboard   Telegram 直接弹出三个按钮：
        │                       ✅ Approve   🔁 Re-prompt   ⏹ Close
        ▼
用户点按钮 -> callback_query -> POST /webhook
        │   answerCallbackQuery 立即停掉转圈；动作入队
        ▼
worker.handle(callback job) -> handle_callback()
        ├─ approve  -> on_approve 钩子（可合并 PR）+ 确认消息
        ├─ reprompt -> Devin POST /sessions/{id}/message 追加指令
        └─ close    -> Devin cancel_session + 注销映射
```

关键实现点：

- **webhook 永远 200**：Telegram/Devin 发消息失败只记日志，绝不返回 5xx，否则上游重试 → 重复通知。
- **callback_data 就是控制通道**：`approve:<sid>` / `reprompt:<sid>` / `close:<sid>`， Telegram 按钮按下后原样回到 `/webhook`，无需维护额外会话状态。
- **归属校验**：按钮只能控制 registry 中属于当前 chat 的 session，别人无法取消你的任务。
- **轮询仅作兜底**：worker 里的状态轮询保留，但完成后的交互走 webhook 事件；Late webhook 仍能通过 registry 找到 chat。

对应文件：`devin_events.py`、`sessions.py`、`completion.py`、`app.py`（`/devin-webhook`）、
`extract.py`（callback_query）、`worker.py`（callback job）、`test_completion.py`。

## 运行

```bash
pip install -r requirements.txt
export WEBHOOK_SECRET="your-secret"
export DEVIN_WEBHOOK_SECRET="your-devin-secret"
export TELEGRAM_BOT_TOKEN="..."
uvicorn app:app --reload
```

Devin 控制台中把 session 事件 webhook 指向 `https://<host>/devin-webhook`，
secret 与 `DEVIN_WEBHOOK_SECRET` 一致。

测试（CI 里跑，不依赖网络与真实 token）：

```bash
pytest -q
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库 + README | ✅ |
| M1 | ingress（webhook + 校验 + 队列 + 测试） | ✅ |
| M2 | voice.py faster-whisper | ✅ |
| M3 | Issue 开单 + 去重 | ✅ |
| M4 | Devin 接入 + 事件回推 | ✅ |
| M5 | 重试上限 + 死循环 + /stop | ✅ |
| M6 | 完成流程：Devin webhook + Telegram 三按钮 + 去重 | ✅ |

测试：43 passed。覆盖签名 401/200、不可处理更新零回复、通知失败仍 200、
Issue 幂等、重试/死循环、Devin 事件归一化、事件去重、callback 提取、三个按钮动作与归属校验。
