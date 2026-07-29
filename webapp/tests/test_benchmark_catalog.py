from __future__ import annotations

import copy

import pytest


def test_production_viscose_s2_catalog_has_39_unique_pairs() -> None:
    from webapp.backend import benchmark_catalog

    catalog = benchmark_catalog.load_catalog()

    assert catalog["schema_version"] == "benchmark_course_catalog.v1"
    assert catalog["catalog_ref"] == "benchmark-catalog:viscose-s2@1"
    assert catalog["benchmark_ids"] == {"easier": 2335, "medium": 2336}
    assert len(catalog["pairs"]) == 39
    assert len({pair["pair_id"] for pair in catalog["pairs"]}) == 39
    for difficulty in ("easier", "medium"):
        scenarios = [pair[difficulty] for pair in catalog["pairs"]]
        assert len({item["scenario_id"] for item in scenarios}) == 39
        assert len({item["scenario_name"] for item in scenarios}) == 39

    bean = benchmark_catalog.pair_for_scenario_profile(
        "scenario:switching.beants_larger@1", catalog=catalog,
    )
    assert bean is not None
    assert bean["easier"]["scenario_name"] == "beanTS Larger"
    assert bean["medium"] == {
        "scenario_id": "viscose-s2:medium:23",
        "scenario_name": "beanTS",
        "scenario_profile_ref": None,
    }


@pytest.mark.parametrize("mutation", ["duplicate_pair", "duplicate_name", "bad_exact_ref"])
def test_catalog_validation_fails_closed_for_ambiguous_identity(mutation: str) -> None:
    from webapp.backend import benchmark_catalog

    catalog = copy.deepcopy(benchmark_catalog.load_catalog())
    if mutation == "duplicate_pair":
        catalog["pairs"][1]["pair_id"] = catalog["pairs"][0]["pair_id"]
    elif mutation == "duplicate_name":
        catalog["pairs"][1]["easier"]["scenario_name"] = catalog["pairs"][0]["easier"]["scenario_name"]
    else:
        catalog["pairs"][0]["easier"]["scenario_profile_ref"] = "scenario:name-only"

    with pytest.raises(ValueError, match="catalog"):
        benchmark_catalog.validate_catalog(catalog)
