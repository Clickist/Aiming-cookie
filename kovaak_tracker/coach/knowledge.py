"""Compatibility signal index generated from the canonical Knowledge Registry."""
from __future__ import annotations

from .knowledge_registry import entry_ref, load_registry, query_registry


def _legacy_knowledge() -> dict[str, dict[str, object]]:
    data = load_registry()
    signals = sorted({
        signal
        for entry in data["entries"]
        if entry["status"] == "active"
        for signal in entry["signals"]
    })
    out: dict[str, dict[str, object]] = {}
    for signal in signals:
        selected = query_registry(data, issue_signal=signal)
        entries = [{
            "entry_ref": entry_ref(entry),
            "entry_version": entry["entry_version"],
            "text": entry["text"],
            "source_ref": entry["sources"][0]["source_ref"],
            "source_level": entry["sources"][0]["source_level"],
            "max_claim_level": entry["max_claim_level"],
            "limitations": list(entry["limitations"]),
            "counterevidence": list(entry["counterevidence"]),
            "supported_uses": list(entry["supported_uses"]),
        } for entry in selected]
        all_signal_entries = [
            entry for entry in data["entries"]
            if entry["status"] == "active" and signal in entry["signals"]
        ]
        community = next(
            (entry["text"] for entry in all_signal_entries
             if any(source["source_level"] == "personal_experience_unverified"
                    for source in entry["sources"])),
            next(
                (entry["text"] for entry in all_signal_entries
                 if any(source["source_level"] in {"community_consensus", "experimental"}
                        for source in entry["sources"])),
                entries[0]["text"] if entries else "",
            ),
        )
        cues = [
            item["text"] for item in entries
            if "training_cue" in item["supported_uses"]
        ]
        out[signal] = {
            "community": community,
            "cues": cues,
            "entries": entries,
            "registry_version": data["registry_version"],
        }
    return out


KNOWLEDGE = _legacy_knowledge()

__all__ = ["KNOWLEDGE"]
