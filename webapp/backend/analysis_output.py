"""Progressive disclosure JSON output for analysis results.

After an analysis completes, the worker calls write_progressive_disclosure()
to emit layered JSON documents to {DATA_ROOT}/analyses/{session_id}/ so Coach
can read them from the file system without going through the API.

Layout is owned by docs/ARCHITECTURE.md §4.1.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from .file_store import _sanitize_json_value

log = logging.getLogger(__name__)

# Distribution stat keys to extract from each metric dict for metrics.json.
# ``med`` is normalised to ``median`` in the output.
_DISTRIBUTION_KEYS = ("p25", "p75", "p90", "mean", "std", "min", "max", "IQR")


def write_progressive_disclosure(
    data_root: str,
    session_id: int,
    result: dict,
    stats_path: str | None = None,
    locale: str = "zh-CN",
) -> None:
    """输出渐进式披露 JSON 文档到 analyses/{session_id}/ 目录。

    Writes overview.json, metrics.json, events.json, evidence.json, and
    stats.txt.  Best-effort: file output failures log a warning but never
    raise, so the analysis result (already committed via mark_done) is not
    affected.  ``locale`` picks the scenario-reading summary wording
    (fail-open to zh-CN).
    """
    try:
        analyses_dir = Path(data_root) / "analyses" / str(session_id)
        analyses_dir.mkdir(parents=True, exist_ok=True)

        _write_json(analyses_dir / "overview.json", _build_overview(session_id, result, locale=locale))
        _write_json(analyses_dir / "metrics.json", _build_metrics(result))
        _write_json(analyses_dir / "events.json", _build_events(result))
        _write_scenario_reading(analyses_dir, result)
        _write_evidence(analyses_dir, session_id, result)
        if stats_path:
            _write_stats(analyses_dir, stats_path)
    except Exception:
        log.warning(
            "progressive disclosure output failed session=%s",
            session_id,
            exc_info=True,
        )


# --- helpers ---------------------------------------------------------------

def _write_json(path: Path, data: object) -> None:
    """Write atomically — Coach reads these files directly mid-write.

    Mirrors file_store.write_json: a crash mid-write must never leave a
    truncated JSON document behind for the Coach's file reads.
    """
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_sanitize_json_value(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _build_data_quality(result: dict) -> dict:
    """[lives 2026-10-05d] 数据质量事实编码，供工程日志与诊断包排查用；
    产品规则：Coach 不向用户转述这些内容（见 coach-system.md 内部工程标注
    不外讲）。

    外部遥测源的导入 known_issues（如 cleaner_short_respawn_merge）+
    结果层 deterministic.limitations 与指标层 metric.limitations 的并集。
    只透传事实信号字段（原样编码，不改写成人读文案、不掺人设）；读取失败
    退化为空列表，绝不影响 overview 产出。
    """
    known_issues: list[str] = []
    snapshot = result.get("input_snapshot") or {}
    source = (snapshot.get("sources") or {}).get("external_telemetry")
    if (
        isinstance(source, dict)
        and isinstance(source.get("external_run_id"), str)
        and source["external_run_id"]
    ):
        try:
            from . import external_telemetry_store as telemetry_store

            meta = telemetry_store.load_meta(source["external_run_id"])
        except Exception:  # noqa: BLE001 - 旁路读取，失败即缺省
            meta = None
        if isinstance(meta, dict):
            quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
            known_issues = [
                str(issue) for issue in (quality.get("known_issues") or [])
                if isinstance(issue, str) and issue
            ]
    deterministic = result.get("deterministic") or {}
    limitations = [
        value for value in (deterministic.get("limitations") or [])
        if isinstance(value, str) and value
    ]
    metrics = deterministic.get("metrics") if isinstance(deterministic.get("metrics"), dict) else {}
    for metric in metrics.values():
        if isinstance(metric, dict):
            limitations.extend(
                value for value in (metric.get("limitations") or [])
                if isinstance(value, str) and value
            )
    return {
        "known_issues": known_issues,
        "limitations": list(dict.fromkeys(limitations)),
    }


def _scenario_reading_descriptor(result: dict) -> dict | None:
    """读取分析输入快照里的诊断读图语境描述符（fail-open：缺/坏即 None）。"""
    snapshot = result.get("input_snapshot")
    if not isinstance(snapshot, dict):
        return None
    descriptor = snapshot.get("scenario_reading_descriptor")
    return descriptor if isinstance(descriptor, dict) else None


def _build_overview(session_id: int, result: dict, locale: str = "zh-CN") -> dict:
    deterministic = result.get("deterministic") or {}
    diagnosis = deterministic.get("diagnosis") or {}
    metrics = deterministic.get("metrics") or {}
    evidence = result.get("evidence") or {}
    snapshot = result.get("input_snapshot") or {}
    scenario_block = result.get("scenario") or {}
    timeline = deterministic.get("timeline") or []

    issues = diagnosis.get("issues") or []
    preroll_raw = result.get("video_decode_preroll_ms")
    preroll_ms = (
        float(preroll_raw)
        if isinstance(preroll_raw, (int, float))
        and not isinstance(preroll_raw, bool)
        and preroll_raw >= 0
        else None
    )
    enriched_issues: list[object] = []
    for issue in issues:
        if isinstance(issue, dict):
            enriched = dict(issue)
            anchors = _time_anchors_for_issue(
                issue, timeline, preroll_ms=preroll_ms,
            )
            if anchors:
                enriched["time_anchors"] = anchors
            enriched_issues.append(enriched)
        else:
            enriched_issues.append(issue)

    video_source = (snapshot.get("sources") or {}).get("video")
    video_evidence: dict[str, object] | None = None
    if isinstance(video_source, dict):
        availability = video_source.get("availability")
        if isinstance(availability, str):
            video_evidence = {"availability": availability}
            reason = video_source.get("reason")
            if isinstance(reason, str) and reason:
                video_evidence["reason"] = reason

    overview: dict[str, object] = {
        "analysis_ref": result.get("analysis_id") or f"analysis:{session_id}",
        "scenario": snapshot.get("scenario"),
        "analysis_type": result.get("analysis_type"),
        "input_mode": result.get("input_mode"),
        "status": "done",
        "completed_at": result.get("completed_at"),
        **(
            {"video_decode_preroll_ms": preroll_ms}
            if preroll_ms is not None
            else {}
        ),
        **(
            {"video_evidence": video_evidence}
            if video_evidence is not None
            else {}
        ),
        "diagnosis": {
            "issues": enriched_issues,
            "headline": _build_headline(diagnosis),
        },
        "metrics_summary": {
            key: _extract_metric_summary(metric)
            for key, metric in metrics.items()
            if isinstance(metric, dict)
        },
        "evidence_availability": {
            "sources": {
                name: src.get("availability")
                for name, src in (evidence.get("sources") or {}).items()
                if isinstance(src, dict)
            },
            "coverage": evidence.get("coverage"),
        },
        # [lives 2026-10-05d] 数据质量事实编码，供工程日志与诊断包排查用；
        # 产品规则：Coach 不向用户转述这些内容（见 coach-system.md 内部工程
        # 标注不外讲）。
        "data_quality": _build_data_quality(result),
        "scenario_info": {
            "support_status": (
                scenario_block.get("support_status")
                or deterministic.get("support_status")
            ),
            "limitations": list(deterministic.get("limitations") or []),
        },
    }
    # 施工单④：诊断读图语境摘要（"这张图练什么"）接进 Coach 主读取面。
    # 只透精炼摘要（3-5 行 + 结构化语义键）；全量描述符在 scenario_reading.json
    # 按需展开。语境只读、绝不参与判型（sce_reading 既有红线）。fail-open：
    # 描述符缺失/损坏时整个键缺席，不影响 overview 产出。
    descriptor = _scenario_reading_descriptor(result)
    if descriptor is not None:
        try:
            from kovaak_tracker.sce_reading import scenario_reading_summary

            overview["scenario_reading"] = scenario_reading_summary(
                descriptor, locale=locale,
            )
        except Exception:
            log.warning(
                "scenario reading summary projection failed session=%s",
                session_id,
                exc_info=True,
            )
    return overview


def _write_scenario_reading(analyses_dir: Path, result: dict) -> None:
    """施工单④：全量读图描述符落为独立详单（按需展开面）。

    仅 availability=available 时写文件；unavailable 事实已在 overview 的
    scenario_reading 摘要里（含 reason）。失败只告警，不阻塞披露输出。
    """
    descriptor = _scenario_reading_descriptor(result)
    if not isinstance(descriptor, dict) or descriptor.get("availability") != "available":
        return
    try:
        _write_json(analyses_dir / "scenario_reading.json", descriptor)
    except Exception:
        log.warning("scenario_reading.json output failed", exc_info=True)


def _time_anchors_for_issue(
    issue: dict, timeline: list, *, preroll_ms: float | None = None,
) -> list[dict]:
    """从 issue 的 event_refs 解析出对应事件的视频时间锚点列表。

    每个锚点带 ``ms``（peak_ms 优先，退化 relative_ms）以及该 issue 关注的
    metric（metric_refs）在对应事件上的值，Coach 据此比较哪个时间点最典型。
    event_ref 形如 ``analysis:{session_id}:event:{event_kind}:{number}``；
    timeline 事件的 ``id`` 形如 ``flick:1``（后缀 ``:{number}`` 对应）。
    ``ms`` 是视频播放时间：challenge-relative 值再减去 capture receipt 的
    decode preroll（MP4 PTS 0 早于 canonical 窗口起点的那段）。
    """
    event_refs = issue.get("event_refs") or []
    metric_refs = issue.get("metric_refs") or []
    anchors: list[dict] = []
    for ref in event_refs:
        if not isinstance(ref, str):
            continue
        parts = ref.split(":")
        if len(parts) < 4:
            continue
        number = parts[-1]
        for event in timeline:
            if not isinstance(event, dict):
                continue
            eid = event.get("id")
            if not isinstance(eid, str) or not eid.endswith(f":{number}"):
                continue
            ms = None
            for key in ("peak_ms", "relative_ms"):
                value = event.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    ms = float(value)
                    break
            if ms is None:
                continue
            if preroll_ms is not None:
                ms = max(0.0, ms - preroll_ms)
            anchor: dict = {"ms": ms}
            metrics = event.get("metrics") or {}
            if isinstance(metrics, dict):
                for mref in metric_refs:
                    if not isinstance(mref, str) or mref not in metrics:
                        continue
                    value = metrics[mref]
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        anchor[mref] = float(value)
            anchors.append(anchor)
            break
    return anchors


def _build_headline(diagnosis: dict) -> str:
    issues = diagnosis.get("issues") or []
    if issues and isinstance(issues[0], dict):
        for field in ("signal", "plain_language_meaning"):
            value = issues[0].get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _extract_metric_summary(metric: dict) -> dict:
    summary = {
        "value": metric.get("value"),
        "unit": metric.get("unit"),
        "classification": metric.get("classification"),
        "availability": metric.get("availability"),
    }
    # generic 视觉指标带的知识桥：指向知识条目的 metric_refs。
    knowledge_refs = metric.get("knowledge_refs")
    if isinstance(knowledge_refs, list) and knowledge_refs:
        summary["knowledge_refs"] = [
            ref for ref in knowledge_refs if isinstance(ref, str)
        ]
    return summary


def _build_metrics(result: dict) -> dict:
    metrics = (result.get("deterministic") or {}).get("metrics") or {}
    output: dict[str, dict] = {}
    for key, metric in metrics.items():
        if not isinstance(metric, dict):
            continue
        entry = {
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "metric_version": metric.get("metric_version"),
            "classification": metric.get("classification"),
            "availability": metric.get("availability"),
        }
        knowledge_refs = metric.get("knowledge_refs")
        if isinstance(knowledge_refs, list) and knowledge_refs:
            entry["knowledge_refs"] = [
                ref for ref in knowledge_refs if isinstance(ref, str)
            ]
        distribution = _extract_distribution(metric)
        if distribution:
            entry["distribution"] = distribution
        output[key] = entry
    return output


def _extract_distribution(metric: dict) -> dict:
    stats: dict[str, object] = {}
    median = metric.get("median", metric.get("med"))
    if median is not None:
        stats["median"] = median
    for key in _DISTRIBUTION_KEYS:
        value = metric.get(key)
        if value is not None:
            stats[key] = value
    return stats


def _build_events(result: dict) -> list[dict]:
    timeline = (result.get("deterministic") or {}).get("timeline") or []
    return [event for event in timeline if isinstance(event, dict)]


def _write_evidence(analyses_dir: Path, session_id: int, result: dict) -> None:
    derived = (result.get("evidence") or {}).get("derived_artifact")
    if not isinstance(derived, dict):
        return
    revision = derived.get("evidence_revision")
    if not isinstance(revision, str):
        return
    try:
        from . import evidence_store

        artifact_file = evidence_store._artifact_file(session_id, revision)
    except Exception:
        return
    if artifact_file.exists():
        tmp_artifact = analyses_dir / ".evidence.json.tmp"
        shutil.copy2(artifact_file, tmp_artifact)
        os.replace(tmp_artifact, analyses_dir / "evidence.json")


def _write_stats(analyses_dir: Path, stats_path: str) -> None:
    source = Path(stats_path)
    if source.is_file():
        shutil.copy2(source, analyses_dir / "stats.txt")
