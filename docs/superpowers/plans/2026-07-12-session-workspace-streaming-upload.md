# Session Workspace + 流式上传 — 可执行施工图

> **状态：completed（2026-07-12，Task 1–3）。**
> **白话目标**：大视频别整文件读进内存；每个分析有自己的文件夹；删分析时文件夹一起清。

## Frozen

1. 每 session 工作区：`{DATA_ROOT}/sessions/{session_id}/`（或创建前用 pending uuid，enqueue 后 rename——选更简单的：**先建 session 行再写文件到 session 目录** 若当前是先写文件后 insert，则改为 insert 得 id 后 stream 写入）。
2. 上传：**流式**写磁盘（`aiofiles` 或 `shutil` 分块），校验 `MAX_VIDEO_BYTES` / `MAX_CSV_BYTES` 用累计字节。
3. 默认 **无自动 TTL**（与 Roadmap 一致）；删除走现有 delete_session + 删目录。
4. manifest 最小：`workspace.json` 记录 video/csv 相对路径与 size（可选 Task）。
5. 不在本 plan 做完整 quota 计费；可加「DATA_ROOT 所在盘 free < 阈值则拒绝上传」简单检查（可选 Task 3）。

## Tasks

### Task 1 — Workspace 路径约定 + delete 删目录

**Allowed:** `webapp/backend/config.py`, `workspace.py`（新建）, `queue.py`, `tests`

**实现:** `session_dir(session_id)`；delete_session commit 后 `rmtree` session 目录（安全：必须在 DATA_ROOT 下）。

### Task 2 — 流式 upload 改写入 session 目录

**Allowed:** `routes.py`, `workspace.py`, `config.py`, `test_routes.py` / upload 相关测

**实现:** 不再 `await video.read()` 整包；分块写；超限中止并清理。

### Task 3 — 低磁盘拒绝（可选最小）+ 回归

**Allowed:** config + routes + tests + PROGRESS

**Verify:** `pytest webapp/tests -q`

---

完成定义：100MB 级上传路径不把整文件持在内存；删 session 工作区干净。
