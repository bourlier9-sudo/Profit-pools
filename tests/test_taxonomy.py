import pytest

from profit_pools.core.taxonomy import Taxonomy, TaxonomyError, build_metadata


def test_layers_in_config_order(meta):
    assert meta.taxonomy.layer_ids == ["PAYER", "PROVIDER", "PHARMACY", "MANUFACTURER", "HEALTH_SERVICES"]


def test_depth_is_not_uniform(meta):
    tax = meta.taxonomy
    # Pharmacy leaves sit directly under the layer; provider leaves are one level deeper.
    assert tax["RX_RETAIL"].depth == 1
    assert tax["PRV_HOSP_PSYCH"].depth == 2
    assert tax.max_depth() == 2


def test_descendants_and_leaves(meta):
    tax = meta.taxonomy
    hosp = tax.leaf_ids("PRV_HOSP")
    assert hosp == ["PRV_HOSP_ACUTE", "PRV_HOSP_PSYCH", "PRV_HOSP_REHAB"]
    assert set(hosp) < set(tax.leaf_ids("PROVIDER"))
    assert tax.leaf_ids("PRV_HOSP_PSYCH") == ["PRV_HOSP_PSYCH"]  # a leaf is its own leaf set
    assert tax.is_ancestor("PROVIDER", "PRV_HOSP_PSYCH")
    assert not tax.is_ancestor("PRV_PHYS", "PRV_HOSP_PSYCH")


def test_path_for_breadcrumbs(meta):
    assert [n.id for n in meta.taxonomy.path("PAY_COMM_IND")] == ["PAYER", "PAY_COMM", "PAY_COMM_IND"]


def test_attribute_inheritance_and_override(meta):
    tax = meta.taxonomy
    assert tax["PAY_COMM_IND"].economics == "payer_pnl"
    assert tax["PAY_COMM_IND"].geo_level == "county"
    # Health Services is top-down by default, but Payer Services overrides it.
    assert tax["HS_BH"].economics == "top_down"
    assert tax["HS_PAYER_SVC"].economics == "margin_on_revenue"
    assert tax["HS_PAYER_SVC"].receives_flows


def test_every_payer_leaf_maps_to_a_unique_lob(meta):
    payer_leaves = meta.taxonomy.leaves("PAYER")
    assert {n.lob for n in payer_leaves} == set(meta.lobs.lobs)
    assert meta.taxonomy.leaf_for_lob("MA_SNP").id == "PAY_MA_SNP"


def test_categories_target_flow_receiving_leaves(meta):
    for c in meta.categories.values():
        node = meta.taxonomy[c.target]
        assert node.is_leaf and node.receives_flows and not node.is_top_down


def test_service_reliability_rules(meta):
    cats = meta.categories
    assert cats["IP_ACUTE"].has_reliable_services("MCD")
    assert not cats["IP_PSYCH"].has_reliable_services("MCD")
    assert not cats["IMAGING"].has_reliable_services("COMM")  # no reliable counts anywhere
    assert not cats["PAYER_SERVICES"].has_reliable_services("COMM")


def test_new_breakdown_is_configuration_only(taxonomy_spec):
    """Subdividing Behavioral Health Services needs no code changes."""
    hs = next(l for l in taxonomy_spec["layers"] if l["id"] == "HEALTH_SERVICES")
    bh = next(c for c in hs["children"] if c["id"] == "HS_BH")
    bh["children"] = [
        {"id": "HS_BH_UM", "label": "Utilization Management"},
        {"id": "HS_BH_ENABLE", "label": "Provider Enablement"},
        {"id": "HS_BH_NETWORK", "label": "Network Management"},
        {"id": "HS_BH_OTHER", "label": "Other Services"},
    ]
    tax = Taxonomy(taxonomy_spec)
    assert tax.leaf_ids("HS_BH") == ["HS_BH_UM", "HS_BH_ENABLE", "HS_BH_NETWORK", "HS_BH_OTHER"]
    assert all(n.economics == "top_down" for n in tax.leaves("HS_BH"))
    assert "HS_BH" not in tax.leaf_ids()


def test_deeper_hierarchy_is_supported(taxonomy_spec):
    prov = next(l for l in taxonomy_spec["layers"] if l["id"] == "PROVIDER")
    hosp = prov["children"][0]
    acute = hosp["children"][0]
    acute["children"] = [{"id": "ACUTE_A", "label": "A", "children": [{"id": "ACUTE_A1", "label": "A1"}]}]
    tax = Taxonomy(taxonomy_spec)
    assert tax["ACUTE_A1"].depth == 4
    assert [n.id for n in tax.path("ACUTE_A1")][:3] == ["PROVIDER", "PRV_HOSP", "PRV_HOSP_ACUTE"]


@pytest.mark.parametrize("mutate, match", [
    (lambda s: s["layers"][0]["children"][0]["children"].append({"id": "PAY_COMM_IND", "label": "dup", "lob": "X"}),
     "Duplicate"),
    (lambda s: s["layers"][0]["children"][0]["children"][0].pop("lob"), "lob"),
    (lambda s: s["layers"][3].update(receives_flows=True), "top-down nodes cannot receive flows"),
    (lambda s: s["layers"][1].update(economics="bogus"), "unknown economics"),
])
def test_invalid_taxonomy_rejected(taxonomy_spec, mutate, match):
    mutate(taxonomy_spec)
    with pytest.raises(TaxonomyError, match=match):
        Taxonomy(taxonomy_spec)


def test_category_target_must_exist(taxonomy_spec):
    prov = next(l for l in taxonomy_spec["layers"] if l["id"] == "PROVIDER")
    prov["children"] = [c for c in prov["children"] if c["id"] != "PRV_AMB"]
    with pytest.raises(TaxonomyError, match="unknown target"):
        build_metadata(taxonomy_spec=taxonomy_spec)


def test_periods(meta):
    p = meta.periods
    assert p.history_years[0] == 2019 and p.history_years[-1] == p.base_year == 2025
    assert p.forecast_years == [2026, 2027, 2028, 2029, 2030, 2031]
