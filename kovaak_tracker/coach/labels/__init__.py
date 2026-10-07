"""B3 i18n 诊断文案目录：zh/en 两侧同构 catalog，键＝稳定 signal/archetype id。

catalog 成员（两侧必须同名同键集，测试锁定）：
- PLAIN_MEANINGS / FINDING_DIAGNOSES / FINDING_PRESCRIPTIONS：advice.py 静态
  回退 Finding 的 plain_language_meaning、diagnosis 模板与处方。
  FINDING_PRESCRIPTIONS 自施工单⑥起只保留"信号→能力域处方"层（不指向图名）。
- CAPABILITY_DOMAINS：能力域 id → 展示名（capability-vocabulary 九域）。
- SPEED_PLAIN_MEANINGS / SPEED_FINDING_DIAGNOSES / SPEED_FINDING_PRESCRIPTIONS：
  施工单⑤速度吞吐判读档下 reverse_ratio 的改写文案。
- TRACKING_*：advice_tracking.py 回退 Finding 同上（含条件片段模板）。
- VERIFICATION：_finalize 填充的可比条件/复测/停止规则（不直接上屏，随
  issue/prescription 落盘进 overview 语料）。
- PRIORITY_REASONS / UNCLASSIFIED：diagnosis.py 硬编码的优先级理由与未分类。
- ARCHETYPE_LABELS / ROOT_CAUSES：profiles.py 冻结回退的画像 label 与根因
  三元组（zh 侧直接引用 profiles 常量，不复制第二份）。

zh 文案＝现行中文逐字搬运（golden 契约要求与迁移前逐字节一致）。
"""
from __future__ import annotations

from . import en as en_us
from . import zh as zh_cn

_CATALOGS = {"zh-CN": zh_cn, "en-US": en_us}


def catalog(locale: str):
    """Return the diagnosis copy catalog for *locale* (unknown -> zh-CN)."""
    return _CATALOGS.get(locale, zh_cn)


__all__ = ["catalog"]
