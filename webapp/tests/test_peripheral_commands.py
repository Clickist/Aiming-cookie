"""Tests for eloshapes.query and peripheral_profile commands."""
from __future__ import annotations

import pytest

from webapp.backend import eloshapes_query


class TestEloshapesQuery:
    def test_weight_filter_excludes_heavy_mice(self):
        result = eloshapes_query.query_mice(weight_max=63.0, limit=5)
        assert result["total_matches"] > 0
        for mouse in result["mice"]:
            assert mouse["weight"] <= 63.0

    def test_size_category_filter(self):
        result = eloshapes_query.query_mice(size_category="fingertip", limit=10)
        for mouse in result["mice"]:
            assert mouse["size_category"] == "fingertip"
        assert result["total_matches"] > 0

    def test_combined_filters(self):
        result = eloshapes_query.query_mice(
            weight_max=63.0,
            size_category="small",
            shape="symmetrical",
            limit=10,
        )
        for mouse in result["mice"]:
            assert mouse["weight"] <= 63.0
            assert mouse["size_category"] == "small"
            assert mouse["shape"] == "symmetrical"

    def test_brand_search(self):
        result = eloshapes_query.query_mice(
            brand_search="logitech",
            weight_max=63.0,
            limit=10,
        )
        assert result["total_matches"] > 0
        for mouse in result["mice"]:
            assert "logitech" in mouse["brand"].lower()

    def test_limit_is_respected(self):
        result = eloshapes_query.query_mice(weight_max=63.0, limit=3)
        assert result["returned"] <= 3
        assert len(result["mice"]) <= 3

    def test_results_sorted_by_weight(self):
        result = eloshapes_query.query_mice(weight_max=63.0, limit=10)
        weights = [m["weight"] for m in result["mice"] if m["weight"] is not None]
        assert weights == sorted(weights)

    def test_jd_mapping_attached_when_available(self):
        result = eloshapes_query.query_mice(weight_max=63.0, limit=50)
        has_jd = any(m.get("jd_product_id") for m in result["mice"])
        assert has_jd, "at least some mice should have JD mapping"

    def test_snapshot_source_is_present(self):
        result = eloshapes_query.query_mice(limit=1)
        assert "snapshot_source" in result
        assert "2026-07-31" in result["snapshot_source"]
