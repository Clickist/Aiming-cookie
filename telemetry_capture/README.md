# telemetry_capture — KovaaK's 目标真值采集工具

> 状态：**已入仓，已接线（2026-09-06）**。本目录自研究工作区
> `Desktop\FPSAimTrainer\analysis\external\` 整体复制（点点拍板：采集工具随产品分发；
> 复制而非移动，研究工作区流程与 RUNBOOK 原路径不受影响）。
> 桌面后端 `webapp/backend/telemetry_capture_service.py` 随运行时常驻拉起三通道
> （脚本自带 --wait/重附着，游戏出现即采），游戏退场后自动 cleaner 分轮 +
> merge 旁车进托管 cleaned 根；watch 根未配置时自动指向该根。安全阀：
> `AIMING_COOKIE_TELEMETRY_CAPTURE=0` 整体停用；诊断快照在
> `diagnostics/telemetry-capture.json`。

## 它是什么

纯外部 `ReadProcessMemory` 读取 KovaaK's 目标/相机遥测（零注入、零写入游戏、
零崩溃风险），配合 OS Raw Input 记录器，产出 Aiming Cookie 外部遥测导入器
（`EXTERNAL_TELEMETRY_IMPORT.md` 所述 ExternalTelemetryRun 管线）消费的 cleaned
轮次目录。Windows 专用，Python 3.9+，**全部仅标准库依赖**。

## 管线与入口

```text
target_poll2.py --run    目标遥测（通道 A，~50Hz）→ target_poll_out_<ts>.jsonl
camera_probe.py --run    相机/POV（通道 B）          → camera_probe_out_<ts>.jsonl
camera_probe.py --calibrate   L2 运行时自校准（换版本后先跑这个）
input_logger.py          OS Raw Input（通道 C，≥500Hz）→ input_log*.jsonl
cleaner.py               清洗分轮                    → cleaned/<源>/round_NN.jsonl + rounds_index.json
merge_channels.py        相机/输入旁车并入轮目录      → views_NN.jsonl / inputs_NN.jsonl / merge_manifest.json
reoffset.py              8 项偏移体检（游戏更新后的判定工具）
```

上游格式合同：`FORMAT.md`（cleaned v2）、`SIDECARS.md`（旁车）。
版本偏移恢复手册：`RUNBOOK_OFFSETS.md`。

## 版本维护义务（重要）

`offsets.json` 按 KovaaK's 主程序 exe 的 sha256 选偏移表。解析走四级链
（`offset_resolver.py`，2026-09-12 起）：

1. 包内表 `offsets.json`（人工策源，随仓库/打包分发）；
2. 用户缓存 `offsets.local.json`（自动定位成功后写入；桌面服务经
   `AIMING_COOKIE_OFFSETS_CACHE` 把它指到 DATA_ROOT，打包版内嵌表只读）；
3. 云表 `https://offsets.aimingcookie.com`（ac-offsets worker，只读、人工策源、
   表内嵌脚本，更新=重新部署；3 秒超时失败静默让位；`AIMING_COOKIE_OFFSETS_URL=0`
   可显式关闭）；
4. 运行时自定位 GUObjectArray（主菜单态签名扫描 + FName 探针可读率 ≥60% 验收，
   通过后写用户缓存）。

**不变量：绝不静默猜偏移**——第 4 级不达验收判据、或全链失败（如游戏未运行），
仍 fail-fast 并指向 `RUNBOOK_OFFSETS.md`。自动推导表只含录制必需的 GUObjectArray
（类槽不在录制主路径上），缺失时 `tp1.calibrate` 报错指回手册。引擎级大更新
（L1/L2 自扫描也失效）仍需人工按 `RUNBOOK_OFFSETS.md` §3 重取（目标 ≤30 分钟，
2026-09-01 / 2026-09-12 两次实战）。

## 刻意未复制的内容

- 全部采集产物（`*_out_*.jsonl`、`input_log*.jsonl`、`rec_cam*.log`、`cleaned/`、
  `validation_*` 等）——运行数据，不入产品仓库；
- 研究快照（`_a2_calib_*`、`_a2_watch_anchors.json`、`thunk_map.json`、
  `_fix0830_snapshot/`）——无运行时代码引用；
- 离线研究工具（`restart_marker.py` 及其 `analysis/perf/perf_probe` 依赖、
  `sce_bb.py` 及 `scenario_meta/`、`target_poll.py` v1、`ai_input.py` SendInput
  测试注入器、census/dump/enum/gvas 等考古脚本）。
