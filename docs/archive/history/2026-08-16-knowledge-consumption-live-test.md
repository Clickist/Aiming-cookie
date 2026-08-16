# 2026-08-16 知识消费面实测（真实 LLM Coach × registry v8）

> 性质：主会话亲自执行的live 测试（点点指定不派 subagent）。环境与 2026-08-15 深测同款：worktree（feat @ d4b2aa5，含批一批二批三全部改动）+ 数据副本（%APPDATA% 复制）+ standalone 三进程（FastAPI 8000 / Coach sidecar 8765 / worker，runtime 配置刷新、uvicorn 直起）+ `coach-cli.mjs` 直连真实 Provider。测试背景：内测用户反馈旧版知识库静默失效导致"建议没用上知识体系"——本测试验证 v8 + 文件化改造后的知识消费面。
>
> **最优先断言**：该召回的场景不允许裸答（静默失效）。

## 一、结果总览

| # | 场景 | 判定 | 要点 |
|---|---|---|---|
| A | 分析讲解轮（"最该改什么"） | ✅ | `get_coach_knowledge`×2 + 真实指标；**M1 修复行为直接体现**："不能只凭输入侧数据下诊断，追踪判断需要视觉数据" |
| B1 | 概念问答 cm/360 | ❌ | **零工具调用裸答**——内容正确但无知识体系支撑 |
| B2 | 指标解读 SPARC | ✅ | 查库 + 读真实数据 + 主动提议"把三条知识库条目串一遍" |
| C | strafe 场景难度原理 | ❌ | 查库×3 **未命中阶梯条目**（信号是库内 token `movement telemetry unavailable`，用户语言映射不到）→ 退化为 LLM 泛讲，0.00v→2.00v 阶梯内容未出现 |
| D1 | "分数涨但准度变差" | ⚠️ | 查库 + history.list 行为正确；但 accuracy-multiplied-scoring 的**乘法计分语义未在回答中突出**，模型偏向自己的"场景变难"解释 |
| D2 | 具名方法 bardpill | ❌ | **"知识库里没有这个词条"→ 自己编了一个解释（与入库内容完全不同）**。根因：检索只有 signal/metric/topic/use 四维，**词条名本身不可查** |
| D3 | overshoot→降 DPI | ✅ | Tier 2 条目全链路通："从知识库看，oversoot 必须你自己确认是视觉上的"（条目边界）+ 单变量实验 + matched retest + 追问区分视觉/体感 |
| E | 训划起草 | ✅ | `training_plan.generate_draft` + `get_coach_knowledge`；基于真实数据（TTK 0→2s、命中率 38–85% 波动）；draft→确认→激活流程合规 |
| F | 知识库全貌 | ✅ | **批二 read 下钻路径工作**：read index.json → 按索引概述，四大类+社区层与 v8 内容一致 |
| — | 物化目录 | ✅ | sidecar 启动自动生成 `knowledge/`（v8 index + 37 entries），无需人工干预 |
| — | claim 措辞 | ✅ | 全程"从知识库看/社区经验"口径，无"研究表明"越级 |

**通过 6 / 部分 1 / 失败 3**。数据/指标类场景（A、B2、D3、E）全过；纯概念与具名词条场景（B1、C、D2）全挂。

## 二、失败三例的共同根因：检索入口没有覆盖"用户语言"

三个失败是同一个病的三个切面——**知识在库里，入口没接通用户怎么说**：

1. **B1（概念定义）**：prompt 只引导"用户提到相关需求时主动调用"，概念题模型觉得"这我知道"就裸答——与用户反馈的"Coach 用自己一套理论解释"同模式。
2. **C（自然语言→信号映射真空）**：批三的召回验证用库内 token（"movement telemetry unavailable"）验证通过，但那是**库内查询形态**的验证；用户问"strafe 为什么难"映射不到任何钩子。**入库验证方法论存在盲区**。
3. **D2（词条名不可查）**：检索四维（signal/metric/topic/use）没有任何维度包含词条名本身；用户引用具名方法（bardpill）时全空。且 miss 后模型自以为懂，**编造了一个与入库内容相悖的解释**——诚实纪律在"它觉得是常识"时失效（对比：查库查不到时它会如实说，行为正确）。

## 三、修复方向建议（未实施）

1. **prompt 补概念引导**（coach-system.md）：解释瞄准/训练概念、术语、具名方法/流派时，先 `read knowledge/index.json` 或 `get_coach_knowledge` 查条目名；**未命中的具名方法如实说"知识库没有"，禁止自行解释**（堵 D2 编造）。
2. **检索加词条名维度**：入库条目把 entry 名与常见别称挂进 topics 或 signal_aliases（"bardpill"、"strafe ladder" 等），使 by-name 查询可命中。
3. **入库流程加自然语言变体验证**：每条新条目的召回测试必须包含 2-3 个用户真实问法（不是库内 token 查询），写进 knowledge 入库的验收标准。
4. （小）D1 类深度问题：scoring 条目的核心语义（分数=命中×准度乘法结构）可考虑在条目 definition 里更前置/更口诀化，提高模型引用率。

## 四、行为亮点（值得保持）

- **M1 修复的行为验证**（A、D3）：input-native 不下视觉诊断的边界两次自然出现——矛盾修复不只是测试绿，真实行为变了。
- **可比性纪律**（B2、E）："没有基线不下好坏结论""复测用同一设置同三张图"——多 DPI 用户案例期待的"先问设置变量"行为在 D1 出现（"最近是不是换了灵敏度、DPI？"）。
- **诚实纪律**（B2）：工具失败后如实说"工具没跑通，结果是我核对的"模式延续。
- 批二 `read index` 新路径（F）与物化（37 条）无人工干预即工作。

## 五、环境与清理

- worktree E:\DevCache\temp\ktest\wt（feat @ d4b2aa5）；数据副本 1.7G（%APPDATA% 复制）；进程 pid 见 ktest-logs。
- 已清理：三进程杀净、junction 摘除（主仓 node_modules 无损）、worktree removed、数据副本与日志删除（可再生测试产物）。
- 环境坑补充：Windows PowerShell 5 读无 BOM UTF-8 脚本中文乱码——junction/启动脚本走文件时必须 BOM 或避开中文路径（本次 worktree 移至 E 盘避开）；`/dev/tcp` 探测在 git bash 不可用，健康检查用 curl。
