# 静态 CV 方案调研：KovaaK 视频通用靶子检测（GitHub/开源界）

日期：2026-08-16 · 作者：CV 调研 subagent · 状态：调研 + 四局真实素材实测完成（未跟踪文件，不提交）
**约束更新（点点确认）：本产品非商业产品，AGPL-3.0 可用。许可仅作信息标注，不作筛选条件。若未来商业化需重估。**

## 0. TL;DR

- **社区唯一同题项目** [Ngambarde/aim_trainer_analysis](https://github.com/Ngambarde/aim_trainer_analysis)（AGPL-3.0、2 stars、2025-06 停更）的现成权重**不能直接用**：实测**人形靶零检出**（conf 降到 0.10 仍为 0，其数据集全是球形靶），且黑胶囊 conf 中位仅 0.57。形状覆盖是硬伤，不是调参能救的。
- **ONNX Runtime 是 YOLO 路线的解锁器**：社区权重（实为 YOLO11n-seg，README 写 YOLOv8 不准）PyTorch CPU 231-266ms/帧 → ONNX **方形 960px 只要 82-93ms/帧，检出与 PyTorch 完全一致**（逐帧 conf 到小数点后两位相同，导出无损）。160s 预算内可行但无余量（隔帧采样 148-167s）。
- **纯 OpenCV「Lab K-means 主导色自动标定」在全部四局（含人形）都工作**，39-66ms/帧，零新依赖，隔帧 70-119s 预算内富余。它是现有检测器家族的"数据驱动自动标定"升级，不是外部依赖。
- **推荐双轨**：短期落地经典自动标定（快、通用、零依赖）；同时把 Ultralytics 生态（现 AGPL 可用）作为**自训练路线**的基础设施——用经典检测器的伪标签 + CC BY 4.0 数据集自训一个覆盖三形状的 YOLO11n，ONNX 部署，作为经典路线失效场景（纹理背景等）的升级件。
- 跟踪：Ultralytics 自带 BYTETrack/BoT-SORT 集成（`model.track`），AGPL 解禁后也可用；单独引包则有 MIT 的 BYTETrack/OC-SORT。

## 1. 问题与方法

需求：1080p60 KovaaK 录像，三种靶形（球/竖胶囊/人形）× 任意颜色 × 任意纯色/分色背景；靶子出生/死亡事件；本地 CPU；单局预算 ~160s；Windows 桌面应用打包；非商业产品（AGPL 可用）。

方法：GitHub/API 调研候选 → 两个代表路线（社区微调模型 vs 纯 OpenCV 自动标定）在**四局真实素材**上采样对比 + IoU 交叉验证 + ONNX 部署实测 + 导出保真度验证。

## 2. 候选清单

### 2.1 社区同题项目

| 项目 | stars | 许可 | 维护 | 说明 |
|---|---|---|---|---|
| [Ngambarde/aim_trainer_analysis](https://github.com/Ngambarde/aim_trainer_analysis) | 2 | AGPL-3.0（现无碍） | 2025-06 停更 | 同题：KovaaK 视频 → 靶分割 + OCR + flick/adjust 指标。仓库自带 `Models/best.pt`（YouTube 素材微调，**架构实为 YOLO11n-seg**，2.83M 参数）。经验值：nano>medium（小数据集）、中心 1024² 裁剪规避 HUD、方形裁剪训练（见 §3 方形效应）、imgsz 保分辨率优先 |
| RefleK's（Reddit 桌面 app） | — | 未查到源码 | — | 会话统计自动跟踪，非视频 CV，不同赛道 |
| Aimmy / YOLO aimbot 生态 | — | 杂 | — | 实时作弊软件，不适合产品；仅证明领域小模型 CPU 实时检测可行 |

**结论：没有可直接商用的现成项目；现成权重形状覆盖不全（实测人形零检出）。项目价值在经验数据与 AGPL 解禁后的代码参考。**

### 2.2 轻量通用检测模型（许可仅标注）

| 模型 | 许可 | stars/维护 | CPU 实况 | 备注 |
|---|---|---|---|---|
| Ultralytics YOLOv8/11/26 | AGPL-3.0 | 生态最大 | 本轮实测 82-93ms/帧（ONNX 960 方形） | **自训练路线首选生态**：训练/导出/跟踪一体化；产品运行时只需 onnxruntime+权重（勿随包带 torch 训练栈） |
| YOLOX（Megvii） | Apache-2.0 | 10.6k，2025-06 维护 | nano+ONNX/OpenVINO 可 50+ FPS（社区） | 生态/工具链弱于 Ultralytics；AGPL 解禁后优势缩小 |
| RF-DETR（Roboflow） | Apache-2.0 | 9.0k，2026-08 活跃 | Nano ~0.6s/帧（CPU 实测） | 准但太慢，出局 |
| PP-YOLOE / RT-DETR | Apache-2.0 | Paddle 系 | — | 拖 Paddle 全家桶，打包不友好 |
| YOLO-NAS | 代码 Apache-2.0，权重另有限制 | Deci 系 | 中 | 权重许可需逐条核对 |

关键事实：**COCO 预训练类没有"aim trainer 靶"类**。实测验证：COCO yolo11n 直接跑 54006 只有 1.3 检出/帧（kite×22 / sports ball×17 乱蹭）vs 微调模型 3.8-4.3。**任何模型路线都必须微调，无"下载即用"。**

### 2.3 跟踪器

| 跟踪器 | 许可 | stars | 备注 |
|---|---|---|---|
| Ultralytics `model.track`（BYTETrack/BoT-SORT 内置） | AGPL-3.0 | — | AGPL 解禁后最省事的集成 |
| BYTETrack | MIT | 6.6k | 关联算法百行级可自吸收 |
| OC-SORT | MIT | 1.1k | 非线性运动强；靶子是瞬移式出生/死亡，差异不大 |

### 2.4 零训练成熟库

- OpenCV `saliency`：平色合成场景噪声大，不如聚类。
- **没有现成"自动颜色聚类检测"轮子**；Lab/HSV K-means 主导色 + 残差阈值即业界标准做法（本轮候选 B，100 行内）。
- Kornia（Apache-2.0）拖 PyTorch 依赖不划算；scikit-image（BSD-3）felzenszwalb 为备选古典路线。

### 2.5 数据集（自训练燃料）

Roboflow Universe 多个 KovaaK 靶数据集（kovaaks-basic、kovaak、target-image-segmentation-data 等）均 **CC BY 4.0**（商用可、需署名）。**注意：均为球形靶，人形靶数据集未找到** —— 自训练的人形样本需靠自家视频伪标签或人工标注补。

## 3. 真实素材实测（四局全矩阵）

环境：Ryzen 7 5800H（8C16T）· OpenCV 5.0 / ultralytics 8.4 + onnxruntime 1.28 CPU · 采样帧跳过首尾 10% · 中央 88% 裁剪规避 HUD · 素材只读副本 `E:\DevCache\temp\cv-eval\videos\`。
候选 A = 社区 `best.pt`（YOLO11n-seg，ONNX 部署，方形 960，conf 0.25）；候选 B = Lab K-means(K=6, 60 帧标定，占比≥4% 为背景) + 半分辨率距离阈值(30) + 连通域形状过滤。

### 3.1 四局 × 两路线

| 视频（真值） | B 检出/帧 | B 零检帧 | B ar(h/w) | B ms/帧 | A 检出/帧 | A conf 中位 | A ms/帧 |
|---|---|---|---|---|---|---|---|
| 54006 红球×青天（最难） | 3.21 | 2/100 | 0.87（球✓） | 66 | 3.17 | ~0.7-0.8 | 93 |
| 54004 黑球×白底 | 2.64 | 7/100 | 0.667（偏扁⚠待人工核） | 53 | 4.14 | 0.61 | 92 |
| 54008 黑胶囊×白底（单靶居中，真值 ar 0.33-0.47 w/h） | 1.33 | **0/100** | **2.96（=真值倒数✓）**，质心贴中心✓ | 62 | 1.17（方形）/1.31（矩形1280） | 0.574 | 82 |
| 53993 红人形 | 2.28 | 1/100 | 0.368（扁宽⚠待人工核，可能 T 姿） | 39 | **0.00 ❌（conf 0.10 仍 0）** | — | 84 |

- 交叉验证（同帧 IoU>0.4）：54006 B 的检出 **80% 被 A 确认**；54008 A 独有检出 = 0。
- **人形零检出是社区权重的死刑判决**：作者数据集全是球形靶，README 明说 "targets are all spherical"。三形状需求必须自训。
- 54004 的 B 检出（2.64）低于 A（4.14）：小黑球可能被面积阈值裁掉或与暗色 HUD 元素粘连——需人工看可视化图定夺；不影响"红/黑球可检出"的结论。

### 3.2 部署实测（YOLO 路线的关键数字）

| 部署 | 输入 | ms/帧 | 54006 检出/帧 | 备注 |
|---|---|---|---|---|
| PyTorch | 1280 方形 | 231-266 | 3.77-4.29 | 基准 |
| **ONNX Runtime** | **960 方形** | **82-93** | **3.17** | **甜点位：检出≈PyTorch@960，预算内** |
| ONNX Runtime | 1280 方形 | 163-203 | 3.83 | 超预算 |
| ONNX Runtime | 1280×736 矩形 | 52-56 | 2.45 | ⚠矩形陷阱：检出掉 1/3（模型按方形裁剪训练，见下） |
| ONNX Runtime | 960×544 矩形 | 26-30 | 1.59 | 同上，胶囊场景掉到 0.28 |

三个实测发现（都是坑/钥匙）：
1. **导出保真度无损**：同帧同输入下 ONNX 与 PyTorch 检出数、conf 完全一致（小数点后两位相同）。提速是真实的（PyTorch→ONNX 4.6 倍）。
2. **方形/矩形 letterbox 陷阱**：同内容分辨率下，方形输入检出 3.77 vs 矩形 2.40。原因是该模型按 1024×1024 方形裁剪训练，矩形输入是分布外。**自训模型时输入形状要与推理形状一致。**
3. **imgsz 降级杀小靶**：640 时 54006 检出 4.29→1.63。小靶必须 ≥960 有效分辨率。

预算换算（60s@60fps = 3600 帧）：
- B 路线：全帧 140-238s（54006 略超）；**隔帧 70-119s ✓ 富余**（事件粒度 33ms）。
- A 路线（ONNX 960 方形）：全帧 295-335s ✗；**隔帧 148-167s（贴线）**；896 方形或隔 3 帧可到 ~100-130s ✓（事件粒度 33-50ms）。

### 3.3 COCO 零微调验证

COCO yolo11n @1280 直跑 54006：1.3 检出/帧（kite 22 / sports ball 17）。不可用，微调必需。

## 4. 推荐路线（AGPL 解禁后修订）

**双轨：短期经典自动标定立即落地；中期用 Ultralytics 生态自训三形状模型作为升级件。**

1. **短期（本周可落地）**：K-means(6)×60 帧标定 → 占比≥4% 簇为背景 → Lab 距离阈值 → 现有连通域/形状过滤 → 现有事件逻辑。OpenCV-only、39-66ms/帧、四局全过（含社区模型失明的人形）。对"任意颜色"按构造免疫（不看色相，只看离背景远不远）——正是下午 HSV 原型在红靶×灰天空翻车的根因。
2. **中期（自训 YOLO11n-seg，Ultralytics 生态）**：
   - 数据：Roboflow CC BY 球靶数据集 + **自家视频用 B 路线产伪标签**（含人形/胶囊——外部数据集没有这两类）+ 人工抽查修正；
   - 训练按方形输入、保 ≥960 有效分辨率（§3.2 的两个坑都来自这里）；
   - 部署只带 onnxruntime + ONNX 权重（~50MB 级），**勿随产品带 torch 训练栈**；
   - 跟踪直接用 `model.track`（BYTETrack）或吸收 MIT ByteTrack 关联算法；
   - 触发条件：内测中 B 路线在真实场景多样性（纹理背景、靶色与背景簇同色）上翻车，或需要比"平色世界假设"更强的鲁棒性时。
3. **不引入**：社区现成权重（人形零检出）、RF-DETR/Paddle 系（慢/重）、COCO 预训练直接用（无靶类）。
4. Ngambarde 项目代码（AGPL 现无碍）：准星运动-匈牙利匹配、flick/adjust 状态机与 Coach 现有做法同构，可作独立复现参考。

### 4.1 许可一览（信息标注，非筛选条件；非商业产品均可用）

| 组件 | 许可 | 商业化时 |
|---|---|---|
| OpenCV / numpy / scikit-image | Apache-2.0 / BSD | ✅ 无碍 |
| Ultralytics YOLO（代码+自训权重） | AGPL-3.0 | 需买商业许可或开源整个桌面应用 |
| YOLOX / RF-DETR 架构 | Apache-2.0 | ✅ |
| BYTETrack / OC-SORT | MIT | ✅ |
| Roboflow Universe KovaaK 数据集 | CC BY 4.0 | ✅ 需署名 |
| Ngambarde best.pt（第三方 AGPL 微调权重） | AGPL-3.0 派生 | 技术上已因人形零检出出局 |

## 5. 风险与未验证项

- 无人工真值：A/B 精度靠交叉验证（80%/71% 一致）+ 可视化人工抽查。**54004 的 B 欠检（2.64 vs 4.14）与 53993 的 B ar=0.368（扁宽）需人工看图定夺**（图已存 out\ 目录）。
- B 路线假设"背景=少数主导色"：纹理背景/动态天空会失效——即中期 YOLO 的触发条件。
- A 路线（自训后）预算贴线：隔帧 960 方形 148-167s，需 896/隔 3 帧留余量；多局排队时叠加。
- ONNX 矩形陷阱与 imgsz 降级坑：自训模型输入形状必须与部署一致，且有效分辨率 ≥960。
- 社区数据集无人形靶：自训的人形样本完全靠自家伪标签，首轮质量决定人形检出上限。
- 本轮未测：GPU 路径（产品定位 CPU 优先）；KovaaK 压缩参数变化（码率）对两路线的影响。

## 6. 产物位置

- 报告：仓库根 `static-cv-github-survey-2026-08-16.md`（本文件，未跟踪）
- 实测脚本：`E:\DevCache\temp\cv-eval\scripts\{eval_yolo,eval_bgsub}.py`；基准/消融代码在临时 venv 会话 heredoc 中（可按需固化）
- 结果/可视化：`E:\DevCache\temp\cv-eval\out\{yolo,bgsub}-{54006,54008,bgsub-54004,bgsub-53993}\`（画框 JPG + summary.json）
- 模型：`E:\DevCache\temp\cv-eval\ngambarde-best.pt`（6.2MB）、`ngambarde-best.onnx`（11.3MB，dynamic）
- 隔离环境（ultralytics+onnxruntime）：`E:\DevCache\temp\cv-eval\venv\`（未触碰仓库 .venv / requirements / 产品代码）
