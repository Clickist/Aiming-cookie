# RUNBOOK_OFFSETS（内部手册，未随开源仓库分发）

本手册为 Aiming Cookie 团队内部运维文档，出于产品与上游游戏 ToS 考虑不随开源仓库分发。

游戏更新后的偏移恢复由四级自适应链自动完成（包内表 → DATA_ROOT 缓存 →
offsets.aimingcookie.com 云表 → GUOA 运行时自定位），全部失败时按 fail-fast
报错并提示人工重取。人工重取流程、RVA 定位与二进制比对方法见内部文档。

—— Aiming Cookie 维护组
