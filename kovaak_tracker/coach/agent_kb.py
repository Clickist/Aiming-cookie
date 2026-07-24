"""Compatibility projection of the canonical versioned Coach Knowledge Registry.

New code should use :mod:`knowledge_registry`. Legacy Agent tools retain their
historic topic/source fields, but no knowledge prose is maintained here.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from .knowledge_registry import entry_ref, load_registry


class KnowledgeChunk(TypedDict):
    topic: str
    topics: list[str]
    signal: str | None
    signals: list[str]
    metric_refs: list[str]
    category: str
    source_ref: str
    source_level: Literal[
        "product_contract",
        "academic_peer_reviewed",
        "community_consensus",
        "personal_experience_unverified",
        "experimental",
    ]
    text: str
    entry_ref: str
    entry_version: int
    registry_version: str
    max_claim_level: str
    limitations: list[str]
    counterevidence: list[str]
    supported_uses: list[str]


def _chunk(entry: dict, registry_version: str) -> KnowledgeChunk:
    primary_source = entry["sources"][0]
    return {
        "topic": entry["topics"][0],
        "topics": list(entry["topics"]),
        "signal": entry["signals"][0] if entry["signals"] else None,
        "signals": list(entry["signals"]),
        "metric_refs": list(entry["metric_refs"]),
        "category": entry["category"],
        "source_ref": primary_source["source_ref"],
        "source_level": primary_source["source_level"],
        "text": entry["text"],
        "entry_ref": entry_ref(entry),
        "entry_version": entry["entry_version"],
        "registry_version": registry_version,
        "max_claim_level": entry["max_claim_level"],
        "limitations": list(entry["limitations"]),
        "counterevidence": list(entry["counterevidence"]),
        "supported_uses": list(entry["supported_uses"]),
    }


_data = load_registry(registry_version="2026-07-14.v1")
KB: list[KnowledgeChunk] = [
    _chunk(entry, _data["registry_version"])
    for entry in _data["entries"]
    if entry["status"] == "active"
]
BY_TOPIC: dict[str, list[KnowledgeChunk]] = {}
for chunk in KB:
    for topic in chunk["topics"]:
        BY_TOPIC.setdefault(topic, []).append(chunk)

__all__ = ["KB", "BY_TOPIC", "KnowledgeChunk"]
