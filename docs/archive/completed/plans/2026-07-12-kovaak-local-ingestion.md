# KovaaK Local Ingestion — 可执行施工图

> **状态：completed（2026-07-12，Task 1）**
> **Task 1：Stats / Performance parser + 本地目录发现基础**
> **来源：** RefleK’s（GPL-3.0）相关实现与行为合同；实现移植到 Aiming Cookie 的 Python/Tauri 边界，不引入 Go/Wails runtime。

## Frozen

1. Aiming Cookie 本项目采用 **GPL-3.0**；保留 RefleK’s 的许可证与来源说明，并在移植模块中标注 upstream provenance。
2. 现有 `kovaak_tracker.csv_parser.parse_stats_csv` 是 Stats CSV 的事实解析入口；本 Task 不重写或复制它。
3. `.performance` 文件实际扩展名为 `.perf`；Task 只解析 RefleK’s 已验证的 protobuf-wire 字段，未知字段必须跳过，不得因未来字段扩展而失败。
4. 本 Task 的 watcher 只负责轮询、去重、稳定文件判断和回调发现结果；不写数据库、不改变 History schema、不触碰前端路由。
5. Stats 与 Performance 通过规范化文件 stem 配对；缺失配对文件时仍可发出单文件发现事件。
6. Raw Input、视频自动匹配、前端入口、Coach 工具、云同步和 benchmark UI 不属于 Task 1。
7. 不复制 RefleK’s Wails/Go runtime；不把 sibling repo 作为 git submodule 或运行时依赖。

## Allowed files

- `LICENSE`（新建）
- `NOTICE`（新建）
- `docs/superpowers/plans/README.md`
- `docs/superpowers/plans/2026-07-12-kovaak-local-ingestion.md`
- `kovaak_tracker/performance_parser.py`（新建）
- `webapp/backend/kovaak_ingest.py`（新建）
- `tests/test_performance_parser.py`（新建）
- `webapp/tests/test_kovaak_ingest.py`（新建）

不得修改其它文件。若必须扩大范围，停止并报告。

## Task 1 — parser 与 watcher 基础

### Tests first

1. 用内存构造的最小 protobuf-wire fixture 验证 `.perf` header、challenge profile、event payload 能解析。
2. 验证未知 protobuf 字段被跳过。
3. 验证损坏/截断 payload 返回明确异常。
4. 验证 watcher 只发现 `.csv` Stats 和 `.perf` Performance 文件，忽略目录和其它扩展名。
5. 验证 watcher 对同一路径去重，并且不会在文件仍持续写入时发出回调。
6. 验证 stats/performance 使用规范化 stem 配对，单文件也能产生发现结果。

### Implementation

- `kovaak_tracker/performance_parser.py`
  - 使用标准库实现最小 protobuf wire reader，不新增 protobuf 运行依赖；
  - 输出 dataclass，包含 header、challenge profile、events；
  - 映射 RefleK’s 已确认 payload 类型；
  - 对未知 field 保持 forward-compatible；
  - 不做诊断和阈值判断。

- `webapp/backend/kovaak_ingest.py`
  - 提供 `.stats/.csv` 和 `.perf` 路径识别；
  - 提供规范化 stem 配对；
  - 提供可停止的 polling watcher；
  - 只在文件 size/mtime 连续两次扫描稳定后发出 callback；
  - callback payload 只包含路径、文件类型、配对路径和解析摘要，不写 DB。

- `LICENSE` / `NOTICE`
  - 加入 GPL-3.0 正文；
  - 明确 Aiming Cookie 与 RefleK’s 相关移植模块的来源和修改状态。

## Verify

```bash
pytest tests/test_performance_parser.py webapp/tests/test_kovaak_ingest.py -q
python -m compileall kovaak_tracker/performance_parser.py webapp/backend/kovaak_ingest.py
cmp -s AGENTS.md CLAUDE.md
git diff --check
git status --short
```

## Stop rule

- 无法用现有 RefleK’s fixture 或构造 fixture 证明字段含义；
- 需要引入新的 protobuf/Go/Wails runtime；
- 需要改变已有 Stats parser、DB schema、History API 或前端；
- 发现 GPL 归属或第三方依赖无法明确；
- 任何超出 Allowed files 的必要改动。

## 完成定义

- Task 1 相关测试通过；
- parser 对已知 `.perf` 样本可解析、对未知字段可兼容；
- watcher 能稳定发现并去重 KovaaK Stats/Performance 文件；
- Aiming Cookie 根目录具有 GPL-3.0 与 RefleK’s 归属说明；
- 不引入 Wails/Go runtime，不改变现有业务 API 和用户路径。
