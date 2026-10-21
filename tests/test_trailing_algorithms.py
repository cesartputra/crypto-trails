from copy import deepcopy
from decimal import Decimal

import pytest

from scenarios import chain_tx, get_scenario, stamp
from schemas import FNAMoneyTrailHandoffInput
from services.bank_trailer import FNAHandoffAdapter, merchant_heuristic
from services.arkham_client import ArkhamClient
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge


def handoff_for(fixture):
    return FNAMoneyTrailHandoffInput(**fixture["handoff"])


def test_fna_adapter_imports_upstream_snapshot_read_only_without_bank_traversal():
    fixture = get_scenario("p2p-full-cycle")
    before = deepcopy(fixture["fna_snapshot"])
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff_for(fixture), fixture)
    assert fixture["fna_snapshot"] == before
    assert len(edges) == len(before["edges"])
    assert all(edge.metadata["imported_read_only"] is True for edge in edges)
    assert all(edge.metadata["bank_traversal_performed_here"] is False for edge in edges)
    mules = [node for node in nodes if node.node_type == "MULE"]
    assert mules
    for node in mules:
        assert node.institution_decision.request_status == "REJECTED_BY_INSTITUTION"
        assert node.institution_decision.approved_frozen_amount_idr == 0
        assert node.institution_decision.institution_category == "PUJK"
        assert "PAKD" in node.institution_decision.institution_decision_notes


@pytest.mark.parametrize("mutation", ["missing_account", "wrong_trail", "duplicate_edge", "wrong_handoff_amount"])
def test_invalid_fna_snapshot_is_rejected_at_import_boundary(mutation):
    fixture = get_scenario("p2p-full-cycle")
    snapshot = fixture["fna_snapshot"]
    if mutation == "missing_account":
        snapshot["nodes"] = [node for node in snapshot["nodes"] if node["id"] != snapshot["edges"][-1]["source"]]
    elif mutation == "wrong_trail":
        snapshot["fna_trail_id"] = "unrelated-trail"
    elif mutation == "duplicate_edge":
        snapshot["edges"].append(deepcopy(snapshot["edges"][-1]))
    else:
        snapshot["edges"][-1]["amount_idr"] = "1"
    with pytest.raises(ValueError):
        FNAHandoffAdapter().import_snapshot(handoff_for(fixture), fixture)


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_rrn", "wrong_gateway", "wrong_amount", "too_early", "late"])
def test_pakd_match_requires_unique_rrn_gateway_amount_and_time(mutation):
    fixture = get_scenario("p2p-full-cycle")
    intake = fixture["pakd_intake_ledger"][0]
    if mutation == "duplicate":
        fixture["pakd_intake_ledger"].append(dict(intake, intake_id="DUPLICATE"))
    elif mutation == "wrong_rrn":
        intake["rrn_reference_number"] = "RRN-UNRELATED"
    elif mutation == "wrong_gateway":
        intake["gateway_account"] = "999888777"
    elif mutation == "wrong_amount":
        intake["fiat_amount_idr"] = "1"
    elif mutation == "too_early":
        intake["timestamp"] = stamp(-1)
    else:
        intake["timestamp"] = stamp(1000)
    with pytest.raises(ValueError):
        P2PAndPAKDBridge().match_handoff(handoff_for(fixture), fixture)


def test_p2p_heuristic_is_explainable_not_an_identity_verdict():
    activity = {"incoming_24h": 120, "outgoing_24h": 125, "exact_amount_ratio": "0.97"}
    result = merchant_heuristic(activity, release_delay_seconds=42)
    assert result["classification"] == "P2P_MERCHANT_CANDIDATE"
    assert result["escrow_time_correlated"] is True
    assert result["score"] > merchant_heuristic(activity)["score"]
    assert merchant_heuristic({"incoming_24h": 1, "outgoing_24h": 0})["classification"] == "INSUFFICIENT_EVIDENCE"
    assert merchant_heuristic(activity, release_delay_seconds=-1)["escrow_time_correlated"] is False
    assert merchant_heuristic(activity, release_delay_seconds=121)["escrow_time_correlated"] is False


def test_escrow_release_cannot_precede_recorded_pakd_fiat_intake():
    fixture = get_scenario("p2p-full-cycle")
    fixture["pakd_intake_ledger"][0]["timestamp"] = stamp(130)
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff_for(fixture), fixture)
    with pytest.raises(ValueError):
        P2PAndPAKDBridge().resolve_onramp(handoff_for(fixture), fixture, nodes, edges)


def prepare_onchain(fixture):
    handoff = handoff_for(fixture)
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff, fixture)
    P2PAndPAKDBridge().resolve_onramp(handoff, fixture, nodes, edges)
    return nodes, edges


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_amount", "wrong_account", "late_release"])
def test_p2p_bridge_rejects_ambiguous_or_inconsistent_escrow(mutation):
    fixture = get_scenario("p2p-full-cycle")
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff_for(fixture), fixture)
    order = fixture["p2p_orders"][0]
    if mutation == "duplicate":
        fixture["p2p_orders"].append(dict(order, order_id="DUPLICATE"))
    elif mutation == "wrong_amount":
        order["fiat_amount_idr"] = "1"
    elif mutation == "wrong_account":
        order["merchant_bank_account"] = "999888777000"
    else:
        order["escrow_release_timestamp"] = stamp(1000)
    with pytest.raises(ValueError):
        P2PAndPAKDBridge().resolve_onramp(handoff_for(fixture), fixture, nodes, edges)


def test_onchain_split_outflows_cannot_exceed_available_seed():
    fixture = get_scenario("p2p-full-cycle")
    fixture["chain_ledger"][0]["crypto_amount"] = "11250"
    fixture["chain_ledger"][1]["crypto_amount"] = "11250"
    nodes, edges = prepare_onchain(fixture)
    with pytest.raises(ValueError):
        ArkhamClient(api_key="").trace_fixture(fixture, nodes, edges)


def test_onchain_transfer_before_initial_withdrawal_is_rejected():
    fixture = get_scenario("p2p-full-cycle")
    fixture["chain_ledger"][0]["timestamp"] = stamp(-1)
    nodes, edges = prepare_onchain(fixture)
    with pytest.raises(ValueError):
        ArkhamClient(api_key="").trace_fixture(fixture, nodes, edges)


def test_proportional_taint_of_clean_and_suspected_funds():
    fixture = get_scenario("p2p-full-cycle")
    fixture["wallets"][0]["clean_balance_crypto"] = "11250"
    fixture["wallets"][0]["balance_crypto"] = "11250"
    nodes, edges = prepare_onchain(fixture)
    ArkhamClient(api_key="").trace_fixture(fixture, nodes, edges)
    wallets = {node.id: node for node in nodes if node.node_zone == "ONCHAIN_WALLET"}
    assert all(wallets[name].taint_score_pct == 50 for name in ["w0", "w1", "w2", "deposit"])
    initial_outflows = [edge for edge in edges if edge.source == "w0"]
    assert sum(Decimal(edge.metadata["tainted_crypto_amount"]) for edge in initial_outflows) == Decimal("5625")


def test_taint_uses_remaining_balance_when_a_later_deposit_arrives():
    fixture = get_scenario("p2p-full-cycle")
    fixture["wallets"][1]["clean_balance_crypto"] = "6750"
    fixture["wallets"][1]["balance_crypto"] = "6750"
    fixture["chain_ledger"] = [
        chain_tx("first-in", "w0", "w1", "6750", 150),
        chain_tx("first-out", "w1", "deposit", "6750", 160),
        chain_tx("later-in", "w0", "w1", "4500", 170),
        chain_tx("later-out", "w1", "deposit", "4500", 180),
    ]
    nodes, edges = prepare_onchain(fixture)
    ArkhamClient(api_key="").trace_fixture(fixture, nodes, edges)
    by_id = {edge.id: edge for edge in edges}
    assert Decimal(by_id["first-out"].metadata["tainted_crypto_amount"]) == Decimal("3375")
    assert Decimal(by_id["later-out"].metadata["tainted_crypto_amount"]) == Decimal("3150")
    deposit = next(node for node in nodes if node.id == "deposit")
    assert deposit.taint_score_pct == Decimal("58.00")


@pytest.mark.parametrize("mutation", ["overcredited", "early_sale", "duplicate"])
def test_offramp_cannot_resolve_inconsistent_or_duplicated_sale(mutation):
    fixture = get_scenario("p2p-full-cycle")
    nodes, edges = prepare_onchain(fixture)
    ArkhamClient(api_key="").trace_fixture(fixture, nodes, edges)
    sale = fixture["p2p_orders"][1]
    if mutation == "overcredited":
        sale["crypto_amount_usdt"] = "99999"
    elif mutation == "early_sale":
        sale["escrow_release_timestamp"] = stamp(100)
    else:
        fixture["p2p_orders"].append(dict(sale, order_id="DUPLICATE-SELL"))
    try:
        detected, _ = P2PAndPAKDBridge().resolve_offramp(fixture, nodes, edges)
    except ValueError:
        return
    assert detected == []
