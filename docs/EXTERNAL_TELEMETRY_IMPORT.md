# 外部遥测导入 — ExternalTelemetryRun（KovaaK's 外部内存读取管线接入）

> **定位：外部数据源的导入合同与使用说明。** 本文定义外部遥测（外部内存读取 +
> OS Raw Input 采集管线，上游称 "cleaned 层"）如何进入 Aiming Cookie 本地数据根，
> 以及导入产物的 `external_run.v1` 数据合同。它不改变 KovaaKRun 主链路的任何行为；
> 两个数据源并行，互不依赖。
>
> 上游数据格式：**外部遥测 FORMAT v2**（`analysis/external/FORMAT.md`，2026-08-30
> 版本钉死；主版本不兼容时本模块 fail-closed）。命名对位：**一个 cleaned 轮次
> （round_NN.jsonl）↔ 一条 ExternalTelemetryRun**，与 "一个 KovaaK Challenge ↔
> 一个 KovaaKRun" 严格对位。

## 1. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 对 `cleaned/` 目录的**只读**监听与导入（递归发现全部 `rounds_index.json`） | 不写上游目录任何文件；不参与 kovaak_tracker 的 Stats/.perf 摄取 |
| 目标通道（通道 A）**确定性汇总**的落盘（T2K 分布、spawns/deaths/timeouts、官方计数对账） | 不算 quiet-RT / 开火节奏 / 甩枪角速度 / tracking error（依赖灵敏度常数、输入通道或细对齐 S，全部留分析侧） |
| 场景标签 **proposal-only 透传**（含置信度 / tie_group / candidates 原样入库） | **绝不写 `config/scenario-overrides.json`**（该存储语义 = 用户+Coach 确认，终身有效；自动标签写入会污染场景识别链） |
| 导入期粗对齐（文件名墙钟锚 ±1s，把轮挂到已有 KovaaKRun 的挑战窗） | 不做细对齐 S（click↔death 互相关）与事件级配对 |

## 2. 启用方式

1. 桌面端启动后，后端默认**未配置** watch 根（服务惰性运行，不影响主链路）。
2. `PUT /api/external-telemetry`（桌面 token）提交一个**绝对路径** watch 根，例如
   `C:\Users\<user>\Desktop\FPSAimTrainer\analysis\external\cleaned`。配置落
   `{DATA_ROOT}/config/external-telemetry.json`，watcher 立即热切换（仿
   kovaak-local-directories 的确认流程）。
3. 首个扫描周期即回填全部既有轮次（1s 轮询；文件连续 2 次 size+mtime 稳定才解析）；
   之后新轮次走同一条代码路径增量导入。watch 根缺失/被移动 → 诊断
   `directory_missing`，后端与主 KovaaK 摄取不受影响。

相关端点（均需桌面 token）：

| 端点 | 作用 |
|---|---|
| `GET /api/external-telemetry` | 当前配置 + watcher 健康（与主 KovaaK watcher 诊断互相独立） |
| `PUT /api/external-telemetry` | 设置/更换 watch 根（`{"watch_root": "<绝对路径>"}`） |
| `GET /api/external-runs` | ExternalTelemetryRun 列表（浅投影，含 proposal 不确定态） |
| `GET /api/external-runs/{external_run_id}` | 单条完整 meta |

## 3. 身份、幂等与冻结副本

```text
dedup_key       = "<source_file>|<round>|<index 目录名>"   # 路径无关
external_run_id = "ext-" + sha256(dedup_key)[:16]
content_hash    = sha256(round_NN.jsonl 字节)
```

- 台账：`{DATA_ROOT}/external/_ledger.json`（原子写）。同 key 同 hash → skip（幂等）；
  同 key 不同 hash（cleaner 重洗）→ **content revision**：id 不变，旧 hash 进
  `meta.revisions[]`。
- **冻结副本**：帧数据复制到 `{DATA_ROOT}/external/ext-<id>/round.jsonl`
  （copy-on-import，约 6MB/35 轮量级）。上游 cleaned/ 是实验管线派生物，可能被
  重洗或清理；AC 历史不随上游消失。
- 无 `rounds_index.json` 覆盖的孤立 round 文件（上游 batch index 是**重写非追加**，
  覆盖可能整体丢失）也会导入：帧统计合成元数据，`quality.known_issues` 标
  `missing_rounds_index`，不虚构 tid/lives（T2K 不可用会显式为空）。
- 被拒绝的 index（版本不支持等）只进台账（`status: rejected`），不产 meta——
  fail-closed 但可观测。

## 4. `external_run.v1` meta 合同（`external/ext-<id>/meta.json`）

```jsonc
{
  "schema_version": "external_run.v1",
  "external_run_id": "ext-…",
  "user_id": "desktop-local",
  "origin": {                       // 身份与上游出处（相对 watch 根的路径）
    "source_file": "target_poll_out_0830_030352.jsonl",
    "round": 3,
    "round_file": "…/round_03.jsonl",
    "index_file": "…/rounds_index.json | null",
    "generator": "cleaner.py",
    "format_version": 1,
    "params": {…}                   // 清洗阈值，复现依据
  },
  "fingerprints": {
    "round_sha256": "…", "round_size": 0, "round_mtime_ns": 0,
    "import_parser_version": "external_run_import.v1"
  },
  "time": {
    "t_start": 0.0, "t_end": 76.1, "duration": 76.1, "n_frames": 2075,
    "epoch_anchor": {               // 粗锚：文件名 MMDD_HHMMSS 本地墙钟
      "method": "filename_wallclock", "epoch_start_est": 1788030232.0,
      "precision_s": 1.0, "year_assumed": 2026
    },
    "sampling_note": "~32Hz variable-dt; max gap 1.05s; never assume fixed dt"
  },
  "counts": { "n_targets": 1, "n_moving_targets": 1, "motion_mix": "moving",
              "spawns": 83, "deaths": 82, "timeouts": 1, "timeout_rate": 0.012 },
  "targets": [ {                    // tid 身份三元组 = (source_file, round, tid)
    "tid": 0, "addr_hex": "0x…",   // addr 仅审计，永不作身份（池化复用）
    "motion": "moving", "birth": 0.0, "death": 117.86,
    "alive_window": [0.0, 117.86], "n_samples": 3769, "n_lives": 84,
    "target_path_length_cm": 47912.8,   // 目标路径；与输入侧 path_length 双语义，已改名
    "domain": {"x": [436.4, 5013.8], "y": [-1149.9, 1069.9], "z": [660.0, 1300.0]},
    "lives": [ {"t_start": 766.36, "t_end": 766.59, "n": 8, "path_cm": 202.5} ]
  } ],
  "quality": {
    "gates": { "format_version": "pass", "frames_readable": "pass|degraded",
               "target_count": "pass|warn|unknown", "duration_positive": "pass" },
    "discarded": {…}, "per_addr_cut_stats": {…},
    "known_issues": ["cleaner_short_respawn_merge", "missing_rounds_index"]
  },
  "scenario_proposal": {            // proposal-only，逐字段透传 + 出处
    "status": "uncertain", "label": "…", "score": 0.573, "margin": 0.005,
    "coverage": 0.74, "score_parts": {…}, "candidates": […], "tie_group": […],
    "generator": "scenario_meta.py label", "generated_at": "…",
    "source": "scenario.json"       // 旁车未到时 = {"source": "pending"}，后到再补
  },
  "pairing": {
    "matched_run_ids": [42],        // 挑战窗求交（±1s）命中的 KovaaKRun
    "pair_confidence": "coarse|null",
    "perf_official": {"scenario_name": "…", "kills": 0, "fired": 0} | null,
    "label_agreement": "agree|disagree|unverifiable"   // disagree = 高价值 QA 信号
  },
  "rollups": {
    "t2k": {"n": 82, "p10": 0.436, "p50": 0.5648, "p90": 1.1535, "mean": 0.684,
             "max": 2.217, "over_2s_share": 0.0361,
             "definition": "…lives born and dying strictly inside [t_start, t_end); …"}
  },
  "frames_path": "external/ext-…/round.jsonl",
  "imported_at": "…", "revisions": []
}
```

**导入侧计算边界**（指示性规则，写进代码注释）：rollup 公式若引用
`calibration_profile`、`window_*`、输入 trace 或相机帧中的任何一个 → 禁止放导入侧。
T2K 口径照抄 session_quant 系列（已对拍）：目标 life 时长（出生→死亡），仅统计轮内
出生且在窗内死亡的 life；最后一帧仍存活记 timeout，不入分布。

## 5. 已知边界与偏差记录

- **上游 index 批量重写**：`cleaned/rounds_index.json` 只含最近一批清洗的 source，
  历史轮会从 index 覆盖中消失 → 孤立 round 文件按 §3 导入并显式标注质量缺口。
- **同名 source 多 index 目录**（如同一 source 的两份清洗副本）：dedup_key 含 index
  目录名，两条身份并存，各记各的元数据。
- **epoch 锚无年份**：文件名只有 MMDD_HHMMSS，导入时假设当前年并显式记录
  `year_assumed`；跨年导入的粗配对会失配（fail-soft，不影响数据本身）。
- **cleaner 已知缺陷传导**：短距重生合并（<2000u）使每局 deaths 少计 0–2 →
  `known_issues: cleaner_short_respawn_merge` 固定标注，下游读它决定展示口径。
- 上游格式版本门：`format_version != 1` 的 index 整体拒绝（含其轮文件的孤立导入），
  台账可观测；次版本加字段容忍忽略。
