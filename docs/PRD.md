# Aiming Cookie — 产品需求文档 (PRD)

> **文档定位** · 建立 2026-07-08
> 这是 Aiming Cookie 的**方向锚** + **原始设想记录**。所有下游文档（spec / plan / 各子系统设计）从此派生。多轮 spec/plan 迭代若与本文冲突，**以本文为准**；本文过时则更新本文，不在下游打补丁。
>
> **维护原则**：产品级决策回写本文；子系统实现细节留在各 spec/plan。

---

## 1. 产品一句话

基于物理 + 运动学的 **KovaaK's 瞄准诊断 + AI 教练**。桌面应用：公平指标 → 三层根因诊断 → 对话式 AI 教练 → 长期进步追踪。

## 2. 为什么做（原始设想）

**创始人的痛**：点点自己是 KovaaK's 玩家（DPI 1600 / 51cm per 360° / FOV 103），苦于瞄准训练缺乏**客观、可量化、个性化**的诊断。现有方案两层都是主观的：社区只有主观体感交流（"今天手感好""感觉甩过了"），没有数据化反馈；**个人教练的教学也凭经验感觉，无法量化**——两层都缺客观数据。学术运动科学有成熟指标（SPARC、Fitts throughput、submovement）但没人产品化给玩家。Aiming Cookie 凭运动学数据做诊断，比主观经验更客观、更科学。

**核心信念**：
1. **公平指标**——跨距离/速度可比的运动学量（decel_frac / SPARC / linearity / throughput），比"命中率"更诚实地暴露问题
2. **减速段是诊断核心**——flick 的"刹车"段最能反映控制质量（社区 Zeonlo / Bardpill + 神经科学 Becker 2020 交叉验证）
3. **AI 教练做个性化诊断**——指标是骨架，教练把指标翻译成"你具体哪里有问题、怎么练"
4. **规则化诊断免费，LLM 教练付费**——最贵的 CV 本地跑不亏钱，LLM 按量收费，freemium 成本结构成立

**张力感知的演变**（理论诚实记录）：早期设想从录像推断"手部张力"（PTC, Pure Tension Coeff），后经审视确认**不成立**——PTC 实为 miss-frame 加速度-误差密度，不直接测肌肉张力。v1 以减速段质量（SPARC 等）为核心诊断，手部张力留待远期手部摄像头验证。详见 tracking-coach spec §2。

## 3. 为谁

**核心用户**：大陆为主 KovaaK's flicking 玩家（world-static clicking / 1w6ts 类），愿意录屏 + 导出 Stats CSV 来获取诊断的认真训练者。

**扩展（后续阶段）**：
- tracking 玩家（v1 重构后接通）
- 国际用户（Phase 3+）
- 远期：愿装手部摄像头的深度用户

**不为**：不愿折腾录屏 / CSV 的纯休闲玩家（获取成本 > 价值）。

## 4. 核心价值主张

| # | 价值 | 实现 |
|---|---|---|
| 1 | 公平指标 | decel_frac / SPARC / linearity / throughput / reverse_ratio / path_efficiency 等（学术锚点：Balasubramanian 2012 / Fitts / Novak 2002） |
| 2 | 三层根因诊断 | 症状 → 物理 → 处方（规则引擎 `advice.py` / `advice_tracking.py`） |
| 3 | AI 教练对话 | tool-use agent（`coach/agent.py`），动态深挖 + KB 检索 |
| 4 | 长期进步追踪 | 趋势 + ④ 渐进式训练计划（`progress.py` / `planning.py`） |
| 5 | freemium 成立 | 规则化诊断免费（本地）；LLM 教练付费（云端，按 token） |

## 5. 产品形态与阶段

### 5.1 形态
- **桌面 hybrid 应用**：Tauri 或 Electron 壳（选型另 spec）+ 本地 Python sidecar（CV + agent 框架）+ 云端（LLM 代理 + 账号 + 数据）
- **登录型回访工具**：完整服务有成本，登录锚定用户 + 计费；画像 / 历史是留存核心

### 5.2 分阶段（详见 IA spec §2.2）
| 阶段 | 形态 | 付费墙 |
|---|---|---|
| **v1 早期** | 开放注册（邮箱 + OTP + 密码） | 无墙，全功能免费 |
| **B freemium** | 公开注册 | CV / 诊断本地免费；**墙立 coach 对话 / 深度诊断 / 长期趋势**（LLM 是收费锚） |
| **C 商业化深化** | **绕过备案**（境外部署 + 大陆访问优化） | 订阅 / credits / 用户自带 key |

> 桌面 hybrid 让 freemium 成本结构成立：最贵的 CV 不在服务器，只有 LLM 要钱。

## 6. 核心体验流程

### 6.1 首次旅程（onboarding）
```
下载安装 → 启动 → login（email + OTP + 设密码）
  ↓ 检测无 history
upload（默认页）
  ├ 引导：需准备 mp4 + KovaaK CSV（附录制提示）
  └ 选文件（本地）→ CSV 自动算 aim profile
  ↓ 开始分析
processing（本地 CV ~160s，可后台 / 可切走）
  ├ 教学时刻：指标科普 + 软件教学（滚动卡片）
  └ 切走兜底：空状态预告卡（history 空 → "完成后出现在这"）
  ↓ 完成时
全局 toast + 顶栏角标（任意页可见，不强制跳转）
  ↓
diagnosis_report（免费 / 无 LLM）
  ├ 画像 + 三层根因 + 指标 + 规则化处方 cues + 图
  └ 底部"跟教练深聊 →"按钮（教练入口）
  ↓ 点入口
coach_dialogue（LLM / agent，D 阶段全有权限）
  └ agent 自动加载最新 session 上下文
  ↓ 结束
history（现在有内容）
```

### 6.2 回访旅程（有 history）
```
启动（已登录）→ 检测有 history → history（默认页）
  ├ 趋势卡 + 诊断列表 + 对话历史
  └ 大"新建分析"按钮
  ↓ 分支
看趋势 / 新开分析 / 继续教练对话
```

### 6.3 关键状态
- **完成通知**：全局 toast + 顶栏角标（任意页可见，不强制跳转）；对有教练权限的用户，分析完教练即时可访问新数据
- **空状态**：首次切走看到的空页给"预告卡"而非空白
- **失败态**：本地 CV 失败 / 云端 LLM 失败 / 网络断，分开写明白 + 重试

## 7. 功能边界

### v1（早期，开放注册）
- flicking 诊断（公平指标 + 三层根因 + 处方）
- coach 对话（agent loop + KB）
- 本地 history（趋势 + 列表 + 删 / 导出 / 导入）
- 开放注册（邮箱 + OTP + 密码）+ login
- 完成通知 + 失败态
- 日志（本地 CV / agent / 云端请求 各层）

### B 阶段（freemium）
- 付费墙（coach / 深度诊断 / 长期趋势）
- credits / 订阅（另 spec）
- 云端 history 同步（跨设备）
- ④ 渐进式训练计划接前端

### C 阶段（商业化深化）
- tracking 接通（v1 重构后）
- 绕过备案，持续境外部署
- 订阅 / 用户自带 key

### 远期（不并入当前，留扩展位）
- 手部摄像头（握姿 / 发力 / 微颤 / 疲劳）
- 本地采集（录屏 + 鼠标事件 pynput / mss）
- 外设推荐（数据驱动佣金）
- 多游戏支持

## 8. 关键 UIUX 决策

> 产品级决策，实现细节在各 spec / plan。

| # | 决策 | 阶段 |
|---|---|---|
| 1 | 默认页动态：无 history → upload，有 → history | v1 |
| 2 | upload 无 profile 表单，CSV 自动算，手改走 settings | v1 |
| 3 | processing 可后台；教学时刻 = 指标科普 + 软件教学；空状态给预告卡 | v1 |
| 4 | diagnosis_report 免费（规则化，含处方 cues），底部"教练入口"按钮 | v1 |
| 5 | coach_dialogue = LLM；D 不立墙，B 立墙（形态待计费 spec） | D→B |
| 6 | history 本地优先，支持删 / 导出 / 导入，云端同步推后 | v1 本地 / B 云 |
| 7 | v1 登录收窄为"计费 + 身份"，不背 history | v1 |
| 8 | 失败态：本地 CV / 云端 LLM / 网络断 分开写明白 | v1 |
| 9 | 日志 cross-cutting：本地 CV / agent / 云端 各层埋 | v1 |
| 10 | 录屏 + 鼠标采集远期；upload 留扩展位 | 远期 |
| 11 | 分析完成：全局 toast + 顶栏角标，不强制跳转 | v1 |
| 12 | 教练即时访问：分析完写入本地 session，coach_dialogue 进入时 agent 自动加载最新上下文 | v1 |
| 13 | upload 修复：视频 / CSV 上传来源文件夹**分别记忆**（当前 bug：共用，导致 CSV 被导向视频目录） | v1 修复 |

## 9. 架构分工（桌面 hybrid）

| 层 | 位置 | 说明 |
|---|---|---|
| 视频解析 + pan_tracker + flick/track 指标计算 | **本地 sidecar** | CPU 密集，搬用户机器省成本 + 解并发 |
| coach agent 框架（tool-use loop / tool handlers / KB 检索） | **本地 sidecar** | 编排逻辑本地跑 |
| LLM 推理请求 | **云端 API 代理** | 藏 key / 按 token 计费 / freemium 计量 |
| 账号 / 订阅 / 画像 / history | **云端**（B+ 阶段） | 跨设备聚合（v1 history 先本地） |

> webapp 既有资产演进不浪费：FastAPI 从"跑分析"瘦身为"账号 / LLM 代理 / 数据"；Worker CV 搬本地 sidecar；Next.js 进桌面壳。

### 9.1 云端部署（方案 A：一台香港小 VPS）

桌面 hybrid 后云端只剩轻量级（鉴权 / LLM 代理 / 计费），无 CV 重活，一台小机足够：

| 组件 | 部署 | 说明 |
|---|---|---|
| landing 落地页 | **Cloudflare Pages**（免费） | 营销 + 下载入口，大陆可访问 |
| 桌面安装包分发 | **GitHub Releases**（免费） | 版本管理 + CDN |
| 后端 API（鉴权 + LLM 代理 + 计费） | **香港轻量 VPS** 跑现有 FastAPI | 复用 webapp slice 1 代码 |
| DB | SQLite（v1 流量小够用）→ B 阶段升 Postgres | 单机 |
| 域名 | 境外注册（不备案） | |

**成本**：香港 2核4G VPS ~¥100/月 + 域名 ~¥6/月 + Cloudflare 免费 + LLM 按 token（DeepSeek 默认）。history v1 本地，B 阶段才上云。

## 10. 成功标准

**产品成功**：
- v1 阶段：开放注册获活跃测试用户（具体规模待定），留存 + 反馈质量高
- 用户认可诊断准确（"这说的就是我"）+ coach 有用（"比我自己看指标懂多了"）
- B 阶段：freemium 转化率（免费 → 付费教练）健康

**技术成功**：
- 分析稳定（失败率可接受，具体阈值待真实数据校准）
- 性能（本地 CV ~160s 可接受）
- 指标可信（用户跨次比较有意义）

## 11. 非目标（明确排除）

- Dashboard 独立页（合并进 history 趋势卡）
- Academy（有真实训练内容前不进导航）
- 社交登录（Apple / Google / GitHub，国际化再加）
- 多游戏（先 KovaaK's）
- 手部摄像头 v1（远期）
- 桌面打包工程细节（Tauri / Electron 选型、签名、自动更新，另 spec）
- 订阅计费 / 支付（另 spec）

## 12. 约束与依赖

- **技术栈**：Python CV（opencv-contrib）+ coach 包 + Next.js 16 / React 19 前端 + FastAPI 云端 + Tauri / Electron 壳
- **合规**：**绕过 ICP 备案**（持续境外部署 + Cloudflare）；不迁国内，靠节点优化大陆体验
- **成本**：CV 本地省服务器；LLM 按 token（DeepSeek 默认，可切 Claude）；部署详见 §9.1（一台 VPS ~¥100/月）
- **大陆访问**：持续香港 + Cloudflare（绕过备案）

## 13. 关联文档

| 文档 | 角色 |
|---|---|
| 本 PRD | **方向锚**（产品级） |
| `docs/product-strategy.md` | 战略 + 商业化 + 远期愿景 |
| `docs/superpowers/specs/2026-07-06-aiming-cookie-ia-redesign-design.md` | 导航 IA + login + 流程细节 |
| `docs/superpowers/specs/2026-07-05-flicking-coach-webapp-design.md` | webapp 原始设计（部分被演进） |
| `docs/superpowers/specs/2026-07-05-tracking-coach-design.md` | tracking 理论审视 + coach 设计 |
| `docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md` | coach agent 设计 |
| 各 `writing-plans` 产出 | 实现计划 |

## 14. 决策日志（关键选择 + 为什么）

- **桌面 hybrid 而非纯 web**：省 CV 服务器成本 + 解并发；LLM / 账号必须云端；资产演进不浪费
- **诊断免费 / 教练付费**：规则化诊断（advise / diagnosis）零 LLM 成本可免费；LLM 是唯一硬成本，作收费锚；切分点干净（`build_report` `backend=None` 跳过 narration）
- **history 本地优先（v1）**：简化 v1；导出 / 导入做手动跨设备；云端同步推后 B 阶段
- **默认页动态分支**：首次无 history 不该看空页 → 默认 upload；回访有 history → 默认 history
- **v1 → B → C 分阶段**：v1 开放注册无门槛（不卡邀请码、不背支付）；桌面让 freemium 成立；C 绕过备案不迁国内
- **flicking 先行 tracking 后接**：flicking 指标体系成熟学术锚；tracking 理论债（PTC 命名）待 v2 重构
