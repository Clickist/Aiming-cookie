# 2026-07-09 Review — coach 运行时

## 健康度 B+ | C: 0  H: 0  M: 3  L: 4

**健康度判定**: 扎实。昨天发现的 H1（ROOT_CAUSES 缺 `decel_frac low`）已在 07-08 修复并验证。本次聚焦 agent loop/tool-use 运行时，发现 3 个 Medium + 4 个 Low，无 Critical/High。主要短板是：终端分支 `last_text` 泄漏（昨天 M1 未修）、缺少累积 token 上限保护、重试范围过宽。

## 昨天修复/残留验证

| 修复项 | 验证结果 | 证据 |
|--------|---------|------|
| H1: ROOT_CAUSES 补 `decel_frac low` | ✅ **已修复** | `profiles.py:79` 现有完整三元组：`("减速段占比过低，撞墙式制动", "减速不足 / 制动粗暴", "练匀减速，把减速段当独立动作")` |
| M1: agent loop 终端分支 last_text 泄漏 | ❌ **残留未修** | `agent.py:229` 仍为 `last_text = resp.content_text or last_text`；终端分支（:240）仍用 `last_text or None` —— 与 max_turns 路径（:247-252 已返回 None）处理不一致 |
| KB 索引名一致性（SPARC 阈值、信号名） | ✅ 完整 | `advice.py` 发出的 12 个信号（`decel_frac high/low`/`sparc low`/`reverse_ratio high` 等）在 `ROOT_CAUSES` + `KNOWLEDGE` 中全部有对应条目；SPARC 阈值 -5.0 三处一致 |

**残留结论**: M1 是唯一未修复项，属 Medium（罕见但一旦触发用户看到半截前导词）。其他 6 项昨修全部验证通过。

---

## 新发现（昨天没报的）

### 🔴 Critical (0)

无。

### 🟡 High (0)

无。昨天 H1 已修复，本次无新 High。

### 🟢 Medium (3)

#### M1. agent loop 终端分支 `last_text` 泄漏（残留）

**文件**: `kovaak_tracker/coach/agent.py:225,229,240`

**问题**: 昨天报告未修复。`last_text` 在 tool-call 分支被赋值为 `resp.content_text or last_text`（:225），在终端分支也是 `resp.content_text or last_text`（:229）。如果：

1. Turn 0：模型调 tool + 产 preamble "让我查一下..." → `last_text = "让我查一下..."`
2. Turn 1：模型不调 tool、`content_text=""`、`stop_reason="end_turn"` → `last_text = "" or "让我查一下..."` = `"让我查一下..."`
3. 返回 `narration = last_text or None` = `"让我查一下..."`

这把前导词当最终讲解返回——与 max_turns_exceeded 路径（:247-252 已返回 None）处理不一致。

**影响**: 罕见（多数 backend 终端响应总有内容），但一旦触发，用户看到半截前导词当最终讲解。

**建议**: 终端分支不用 `last_text`，直接用 `resp.content_text or None`：
```python
# line 229
last_text = resp.content_text or last_text  # 删或改逻辑
# line 240
return {
    "narration": resp.content_text or None,  # 不用 last_text
    ...
}
```

---

#### M2. 缺少累积输入 token 上限保护（防失控烧钱）

**文件**: `kovaak_tracker/coach/agent.py:149-260`

**问题**: `run_agent_loop` 只控制每轮 `max_tokens`（输出上限）和 `max_turns`（轮数上限），**没有累计输入 token 计数**。如果：

- 用户上传超长 CSV/视频元数据进 `diagnosis.meta`
- LLM 反复调 tool，每轮的 messages 越来越长（历史累积）
- 单轮 max_tokens=2048，但 12 轮累积输入可达 50k+ tokens

DeepSeek/OpenAI 按 **输入+输出** 计费，失控 loop 可能单次烧掉 ¥10+（按 DeepSeek ¥1/M input 算）。

**证据**: 
- `run_agent_loop` 无 `input_tokens` 计数器
- `MAX_TURNS_HARD_CAP=12` 但没限制单轮输入大小
- 无测试覆盖超长 payload 的 token 耗尽

**对比**: `max_turns` 硬核兜底（:41, :175）存在，但没算输入侧。

**建议**: 
- 短期：文档化警告（超长 CSV/视频谨慎分析）
- 中期：后端层（`providers.py`）暴露 `usage.input_tokens`，agent loop 累计，超阈值提前 `max_turns_exceeded`
- 长期：用户自带 key（云代理层）+ 前端预算显示

---

#### M3. 异常重试范围过宽，认证失败也重试

**文件**: `kovaak_tracker/coach/providers.py:236-251`

**问题**: `_call_with_retry` 只排除 4xx（`status_code` 在 400-499），其他所有异常都重试。但：

- 认证错误（`openai.AuthenticationError` / `anthropic.AuthenticationError`）是 **5xx** 或 SDK 自定义异常
- SDK 序列化错误（`ValidationError`）也是不可恢复

这些错误重试 3 次 + ~4.5s 无意义，浪费用户时间。

**证据**: 
```python
except Exception as exc:
    status = getattr(exc, "status_code", None)
    is_4xx = isinstance(status, int) and 400 <= status < 500
    if attempt >= _MAX_RETRIES or is_4xx:
        raise
```

`AuthenticationError` 的 `status_code` 通常是 401（已被排除）或 None（SDK 特定），后者会进入重试。

**建议**: 扩展排除列表：
```python
# 不可恢复异常类型
UNRECOVERABLE_TYPES = (
    openai.AuthenticationError,
    anthropic.AuthenticationError,
    # ... 其他认证/权限类异常
)
if isinstance(exc, UNRECOVERABLE_TYPES) or is_4xx:
    raise
```

---

### 🔵 Low (4)

#### L1. `_serialize_diagnosis` 缺 `default=str` — 序列化脆弱性

**文件**: `kovaak_tracker/coach/agent.py:295`

**问题**: `_serialize_diagnosis` 用 `json.dumps(payload, ensure_ascii=False)`（无 default），而 `_serialize_progress`（:303）和 `_serialize_plan`（:320）都有 `default=str`。如果 `meta` 含非 JSON 原生类型（numpy float / datetime），diagnosis 序列化会抛异常被 agent loop 的 `except Exception` 吞掉 → narration=None。

**证据**: 
- `diagnosis.meta` 来源是 `report.py`，可能含 `float("nan")` / numpy 类型
- progress/plan 路径有 `default=str` 保护

**建议**: 统一加 `default=str`：
```python
return json.dumps(payload, ensure_ascii=False, default=str)
```

---

#### L2. `chat_with_coach` 用户消息无输入验证 — prompt injection 风险

**文件**: `kovaak_tracker/coach/agent.py:395-413`

**问题**: `chat_with_coach` 把用户消息直接拼进 `seed_messages`（:402-405），**无任何 sanitization**。恶意用户可注入：

- 系统提示覆盖（某些后端支持 `system` role 在 messages 里）
- 特殊 token / control sequences（JSON 注入）

**证据**:
```python
seed = [
    {"role": m.role, "content": m.content}
    for m in messages
    if m.content  # 只过滤空消息，不验证内容
]
```

**风险**: 当前通过 webapp 调用（前端可控），但若暴露 CLI/API 入口，攻击者可注入。

**建议**: 
- 短期：文档化警告（`ChatMessage.content` 应为普通文本）
- 中期：长度限制 + 特殊字符过滤

---

#### L3. tool dispatch 异常处理暴露内部 schema

**文件**: `kovaak_tracker/coach/agent_tools.py:368-377`

**问题**: `ToolBundle.dispatch` 的 `TypeError` exception handler 返回：
```python
{"error": "bad arguments", "detail": str(e),
 "schema": schema["function"]["parameters"]}
```

`detail: str(e)` 可能暴露内部实现细节（如 `missing required field 'signal'`），`schema` 返回完整参数结构——虽不泄露密钥，但对调试过度友好。

**建议**: 生产模式降级：只返回 `"error": "bad arguments"` + `valid_tools`，不附 detail/schema。

---

#### L4. `providers.json` deepseek base_url 与代码默认值不一致

**文件**: `kovaak_tracker/coach/providers.json:12` vs `providers.py:358`

**问题**: 
- `providers.json`: `"base_url": "https://api.deepseek.com"`（无 `/v1`）
- `DeepSeekBackend.__init__` 默认 + `load_backend` fallback: `"https://api.deepseek.com/v1"`（有 `/v1`）

DeepSeek 两个路径都支持（已验证），功能不受影响，但配置和代码默认值不一致容易混淆。

**建议**: 统一（推荐 providers.json 加 `/v1`，与 openai entry 对齐）。

---

## 决策项复查

| 决策项 | 昨天结论 | 今天验证 | 备注 |
|--------|---------|---------|------|
| agent loop tool_calls 单条件 | ✅ 正确 | ✅ 无变化 | `agent.py:196` 仅检查 `resp.tool_calls`，不依赖 `stop_reason`——DeepSeek 长上下文路径正确 |
| max_turns preamble 丢弃 | ✅ 正确 | ✅ 无变化 | max_turns 路径（:247-252）返回 None；测试 `test_max_turns_exhaustion_with_partial_text` 覆盖 |
| 三后端 messages_create 路径 | ✅ 正确 | ✅ 无变化 | Anthropic/DeepSeek/OpenAICompat 三者齐备 |

---

## 跨域 pattern 提示

1. **序列化一致性**: `_serialize_*` 函数在 agent.py 中有 3 个（diagnosis/progress/plan），但只有 progress/plan 有 `default=str`——应统一。
2. **异常分类 granularity**: `_call_with_retry` 按 `status_code` 粗分 4xx vs 其他，但 SDK 自定义异常（认证/序列化）未细分——应扩展为白名单机制（只重试网络/超时/5xx）。
3. **token 计费盲区**: agent loop 只限输出（`max_tokens`），不限输入累积计费——与 webapp budget 机制（只计输出）同构，但桌面 hybrid 阶段（本地 agent + 云 LLM）用户自付成本，需更透明的预算提醒。

---

## vs 昨天（H 数变化、是否回归）

| 维度 | 昨天（07-08） | 今天（07-09） | 变化 |
|------|--------------|--------------|------|
| Critical | 0 | 0 | — |
| High | 1 | 0 | **-1**（H1 已修） |
| Medium | 4 | 3 | **-1**（M1 残留计为 1 项；M2/M3 为新发现） |
| Low | 6 | 4 | **-2**（L1/L2/L3/L4 为新发现；部分昨日 Low 可能已处理或降级） |

**无回归**: 昨天修复的 7 项全部验证通过；昨天报告的 H1 确认已修复。

**遗留风险**: 
- M1（终端分支 last_text 泄漏）残留，应与 H1 同优先级修复（~5 行改动）
- M2（累积 token 上限）是成本控制盲区，桌面 hybrid 阶段用户自付成本，更敏感

---

## Top 3 最该修

| 优先级 | finding | 工作量 | 理由 |
|--------|---------|--------|------|
| 1 | **M1**: agent loop 终端分支 last_text 泄漏 | ~5 行 | 昨天遗留，与 max_turns 路径处理不一致；罕见但用户可见 |
| 2 | **M2**: 累积输入 token 上限保护 | ~20 行 | 防失控烧钱（单次可能 ¥10+）；桌面 hybrid 阶段成本敏感 |
| 3 | **M3**: 异常重试范围过宽 | ~10 行 | 认证失败重试浪费 ~4.5s；用户体验差 |

---

## 测试覆盖验证

运行 `tests/coach/test_agent.py` + `test_agent_chat.py`：**19 passed**（0.14s）。

覆盖情况：
- ✅ tool call → result → end_turn 完整流程
- ✅ max_turns 耗尽返回 None
- ✅ max_tokens 截断返回 None
- ✅ 未知 tool/signal/topic 返回 valid_* 提示
- ✅ backend 异常返回 None
- ✅ chat 多轮 history 传递
- ✅ 空消息跳过

**未覆盖**：
- ❌ 终端分支 last_text 泄漏（需新增 test）
- ❌ 超长 payload 累积 token（需 mock backend.usage.input_tokens）
- ❌ 认证异常重试（需 mock 抛 AuthenticationError）

---

## 执行摘要

**健康度**: B+（扎实，昨修 H1 无残留，本次 3M+4L）

**一句话**: agent loop/tool-use 运行时核心逻辑正确，测试覆盖充分（19/19 passed），但遗留 1 个 Medium（终端分支 last_text 泄漏）+ 发现 2 个新 Medium（累积 token 上限 + 异常重试过宽）+ 4 个 Low（序列化/prompt injection/schema 暴露/配置不一致）。

**建议动作**:
1. 修 M1（终端分支 last_text），与 max_turns 路径对齐
2. 补 M2 的测试用例（超长 payload），文档化警告
3. 扩展 M3 的异常白名单（认证/序列化不重试）
