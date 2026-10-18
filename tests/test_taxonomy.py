import pytest

from profit_pools.core.taxonomy import Taxonomy, TaxonomyError, build_metadata


def test_layers_in_config_order(meta):
    assert meta.taxonomy.layer_ids == ["PAYER", "PROVIDER", "PHARMACY", "MANUFACTURER", "HEALTH_SERVICES"]


def test_levels_follow_source_data_shape(meta):
    """Payer & Manufacturer L1–L2, Health Services L1–L3, Provider & Pharmacy up to L4."""
    tax = meta.taxonomy
    depth = {layer: {n.depth for n in tax.leaves(layer)} for layer in tax.layer_ids}
    assert depth["PAYER"] == {2}
    assert depth["MANUFACTURER"] == {2}
    assert depth["HEALTH_SERVICES"] == {3}
    assert max(depth["PROVIDER"]) == 4 and len(depth["PROVIDER"]) > 1   # ragged
    assert max(depth["PHARMACY"]) == 4 and len(depth["PHARMACY"]) > 1
    assert meta.max_levels == 4


def test_descendants_and_leaves(meta):
    tax = meta.taxonomy
    hosp = tax.leaf_ids("PRV_HOSP")
    assert hosp == ["PRV_IP_MEDSURG", "PRV_IP_MATERNITY", "PRV_OP_ED", "PRV_OP_SURG", "PRV_OP_OTHER",
                    "PRV_HOSP_PSYCH", "PRV_HOSP_REHAB"]
    assert set(hosp) < set(tax.leaf_ids("PROVIDER"))
    assert tax.leaf_ids("PRV_HOSP_PSYCH") == ["PRV_HOSP_PSYCH"]  # a leaf is its own leaf set
    assert tax.is_ancestor("PROVIDER", "PRV_HOSP_PSYCH")
    assert not tax.is_ancestor("PRV_PHYS", "PRV_HOSP_PSYCH")


def test_path_for_breadcrumbs(meta):
    assert [n.id for n in meta.taxonomy.path("PAY_COMM_IND")] == ["PAYER", "PAY_COMM", "PAY_COMM_IND"]
    assert meta.taxonomy.level_ids("PRV_OP_ED", 4) == ["PRV_HOSP", "PRV_HOSP_ACUTE", "PRV_OP", "PRV_OP_ED"]
    assert meta.taxonomy.level_ids("PAY_COMM_IND", 4) == ["PAY_COMM", "PAY_COMM_IND", None, None]


def test_attribute_inheritance_and_override(meta):
    tax = meta.taxonomy
    assert tax["PAY_COMM_IND"].economics == "payer_pnl"
    assert tax["PAY_COMM_IND"].geo_level == "county"
    # PBM services sits under Pharmacy but overrides it: top-down, no flows.
    assert tax["RX_PBM_COMM"].layer_id == "PHARMACY"
    assert tax["RX_PBM_COMM"].economics == "top_down" and not tax["RX_PBM_COMM"].receives_flows
    assert tax["RX_MAIL"].economics == "margin_on_revenue" and tax["RX_MAIL"].receives_flows
    assert all(n.economics == "top_down" for n in tax.leaves("HEALTH_SERVICES"))


def test_every_payer_leaf_maps_to_a_unique_lob(meta):
    payer_leaves = meta.taxonomy.leaves("PAYER")
    assert {n.lob for n in payer_leaves} == set(meta.lobs.lobs)
    assert meta.taxonomy.leaf_for_lob("MA_SNP").id == "PAY_MA_SNP"


def test_categories_target_flow_receiving_leaves(meta):
    for c in meta.categories.values():
        node = meta.taxonomy[c.target]
        assert node.is_leaf and node.receives_flows and not node.is_top_down


def test_every_recipient_leaf_has_exactly_one_category(meta):
    recipients = {n.id for n in meta.taxonomy.leaves() if n.receives_flows}
    assert sorted(c.target for c in meta.categories.values()) == sorted(recipients)


def test_instances_never_on_pharmacy(meta):
    for c in meta.categories.values():
        if meta.taxonomy[c.target].layer_id == "PHARMACY":
            assert not c.has_instances
    assert meta.categories["IP_MEDSURG"].has_instances
    assert not meta.categories["IMAGING"].has_instances   # provider edge case


def test_datasets_and_exclusions(meta):
    assert meta.default_dataset == "MARKET"
    ent = {n.id for n in meta.leaves_in_dataset("ENTERPRISE")}
    assert not any(meta.taxonomy[i].layer_id in ("MANUFACTURER", "HEALTH_SERVICES") for i in ent)
    assert "RX_PBM_COMM" not in ent and "RX_MAIL" in ent
    assert len(meta.leaves_in_dataset("MARKET")) == len(meta.taxonomy.leaves())
    assert meta.datasets["ENTERPRISE"].funding_sources == ()


def test_funding_sources(meta):
    fs = meta.funding_sources
    assert fs["FS_UNINSURED"].basis == "population_pmpm"
    assert fs["FS_SUPPLEMENTAL"].basis == "lump_sum"
    targets = meta.supplemental_targets("FS_SUPPLEMENTAL")
    assert targets and all(meta.taxonomy.is_ancestor("PRV_HOSP", t) for t in targets)


def test_new_breakdown_is_configuration_only(taxonomy_spec):
    """Subdividing Behavioral Health Services needs no code changes."""
    mfr = next(l for l in taxonomy_spec["layers"] if l["id"] == "MANUFACTURER")
    devices = mfr["children"][1]["children"][0]
    devices["children"] = [{"id": "MFR_DEV_CARDIO", "label": "Cardiovascular"},
                           {"id": "MFR_DEV_ORTHO", "label": "Orthopedic"}]
    tax = Taxonomy(taxonomy_spec)
    assert tax.leaf_ids("MFR_DEVICE") == ["MFR_DEV_CARDIO", "MFR_DEV_ORTHO"]
    assert all(n.economics == "top_down" for n in tax.leaves("MFR_DEVICE"))
    assert "MFR_DEVICE" not in tax.leaf_ids()


def test_tree_supports_any_depth_but_contract_limit_is_enforced(taxonomy_spec):
    hs = next(l for l in taxonomy_spec["layers"] if l["id"] == "HEALTH_SERVICES")
    um = hs["children"][0]["children"][0]["children"][0]
    um["children"] = [{"id": "HS_UM_A", "label": "A", "children": [{"id": "HS_UM_A1", "label": "A1"}]}]
    tax = Taxonomy(taxonomy_spec)          # the tree itself has no depth limit
    assert tax["HS_UM_A1"].depth == 5
    with pytest.raises(TaxonomyError, match="levels deep"):
        build_metadata(taxonomy_spec=taxonomy_spec)   # the L1–L4 contract does


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


def test_recipient_leaf_without_category_rejected(taxonomy_spec):
    prov = next(l for l in taxonomy_spec["layers"] if l["id"] == "PROVIDER")
    prov["children"][-1]["children"].append({"id": "PRV_AMB_LAB", "label": "Labs"})
    with pytest.raises(TaxonomyError, match="no claims category"):
        build_metadata(taxonomy_spec=taxonomy_spec)


def test_periods(meta):
    p = meta.periods
    assert p.history_years[0] == 2019 and p.history_years[-1] == p.base_year == 2025
    assert p.forecast_years == [2026, 2027, 2028, 2029, 2030, 2031]


def test_methodology_text_covers_every_dataset(meta):
    from profit_pools.core.config import load_config
    text = load_config("methodology")
    for ds in meta.datasets:
        assert text["provider_revenue"][ds].strip()
    assert "instances" in text["provider_revenue"]["volume"]
