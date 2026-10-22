from datetime import datetime
from decimal import Decimal

import pytest

SCENARIOS = [
    ("p2p-full-cycle", "180000000"),
    ("direct-va-loopback", "120000000"),
    ("p2p-dex-cold-wallet", "250000000"),
    ("golden-period", "95000000"),
    ("bridge-ethereum-arbitrum", "120000000"),
    ("bridge-ethereum-bsc", "120000000"),
]


def amount(value):
    return Decimal(str(value))


def test_complete_fna_handoff_presets(scenarios):
    assert set(scenarios) == {name for name, _ in SCENARIOS}
    required = {
        "iasc_ticket_id", "fna_trail_id", "victim_pujk_name", "victim_account_number",
        "victim_account_name", "last_fna_pujk_name", "last_fna_account_number",
        "pakd_gateway_va_or_account", "fiat_transfer_amount_idr", "fiat_transfer_timestamp",
        "rrn_reference_number", "scenario_id",
    }
    for item in scenarios.values():
        assert required <= item["handoff"].keys()
        assert item["title"] and item["description"]
        assert "report" not in item


@pytest.mark.parametrize("scenario_id,total", SCENARIOS)
def test_graph_integrity_and_no_money_is_frozen_by_tracing(trace, scenarios, scenario_id, total):
    graph = trace(scenario_id)
    assert graph["simulation"] is True
    assert graph["iasc_ticket_id"] == scenarios[scenario_id]["handoff"]["iasc_ticket_id"]
    assert graph["fna_trail_id"] == scenarios[scenario_id]["handoff"]["fna_trail_id"]
    assert graph["fna_handoff"]["rrn_reference_number"] == scenarios[scenario_id]["handoff"]["rrn_reference_number"]
    assert amount(graph["total_scam_idr"]) == amount(total)
    assert 0 <= amount(graph["recoverable_amount_idr"]) <= amount(graph["total_traced_idr"]) <= amount(total)
    assert amount(graph["confirmed_frozen_amount_idr"]) == 0
    assert 0 <= amount(graph["recovery_probability_pct"]) <= 100
    node_ids = {node["id"] for node in graph["nodes"]}
    assert len(node_ids) == len(graph["nodes"])
    assert len({edge["id"] for edge in graph["edges"]}) == len(graph["edges"])
    assert {node["node_zone"] for node in graph["nodes"]} == {
        "FIAT_BANK", "CRYPTO_EXCHANGE_BRIDGE", "ONCHAIN_WALLET"
    }
    for edge in graph["edges"]:
        assert {edge["source"], edge["target"]} <= node_ids
        assert amount(edge["amount_idr"]) >= 0
        assert amount(edge["crypto_amount"]) >= 0
        if edge["edge_type"] == "ONCHAIN_TRANSFER":
            assert edge["tx_hash"]
    for node in graph["nodes"]:
        if node["taint_score_pct"] is not None:
            assert 0 <= amount(node["taint_score_pct"]) <= 100
        assert amount(node["balance_crypto"]) >= 0
    for request in graph["governance_requests"]:
        assert request["target_node_id"] in node_ids
        assert request["request_status"] == "NOT_SENT"
        assert amount(request["approved_frozen_amount_idr"]) == 0
        assert request["decision_timestamp"] is None
    assert "recovery_actions" not in graph


@pytest.mark.parametrize("scenario_id,_", SCENARIOS)
def test_pakd_trails_expose_masked_kyc_and_withdrawal_evidence(trace, scenario_id, _):
    graph = trace(scenario_id)
    assert graph["pakd_trails"]
    required = {"pakd_name", "intake_mechanism", "pakd_user_uid", "kyc_nik_masked",
                "fiat_in_idr", "crypto_purchased_token", "crypto_purchased_amount",
                "remaining_pakd_balance_idr", "withdrawal_status", "withdrawal_tx_hash",
                "blockchain_network", "initial_wallet_address"}
    for record in graph["pakd_trails"]:
        assert required <= record.keys()
        assert record["pakd_name"] and record["pakd_user_uid"]
        assert "********" in record["kyc_nik_masked"]
        assert len(record["kyc_nik_masked"]) == 16
        assert amount(record["remaining_pakd_balance_idr"]) >= 0
        if record["withdrawal_status"] == "WITHDRAWN_ONCHAIN":
            assert record["withdrawal_tx_hash"] and record["initial_wallet_address"] and record["blockchain_network"]
            assert any(edge["tx_hash"] == record["withdrawal_tx_hash"] for edge in graph["edges"])


def test_full_p2p_cycle_resolves_final_bank_and_its_own_pujk_request(trace):
    graph = trace("p2p-full-cycle")
    detections = graph["p2p_detections"]
    assert {item["p2p_direction"] for item in detections} == {"ON_RAMP_DEPOSIT", "OFF_RAMP_WITHDRAWAL"}
    on_ramp = next(item for item in detections if item["p2p_direction"] == "ON_RAMP_DEPOSIT")
    off_ramp = next(item for item in detections if item["p2p_direction"] == "OFF_RAMP_WITHDRAWAL")
    assert on_ramp["merchant_id"] == "MCH-8821"
    assert amount(on_ramp["crypto_amount_usdt"]) == 11250
    assert on_ramp["order_id"] and on_ramp["scammer_uid"] and on_ramp["evidence"]
    assert off_ramp["linked_final_bank_account"] == "034101009988111"
    final = next(node for node in graph["nodes"] if node.get("account") == "034101009988111")
    requests = [item for item in graph["governance_requests"] if item["target_node_id"] == final["id"]]
    assert len(requests) == 1
    assert requests[0]["institution_category"] == "PUJK"
    assert requests[0]["institution_name"] == "BRI"
    assert amount(requests[0]["requested_amount_idr"]) == 144000000
    assert any(record["intake_mechanism"] == "P2P_OFF_RAMP_SETTLEMENT" for record in graph["pakd_trails"])


def test_direct_va_loopback_targets_indodax_decision_authority(trace):
    graph = trace("direct-va-loopback")
    assert "DIRECT VA" in graph["route_badges"]
    assert graph["p2p_detections"] == []
    assert any(record["intake_mechanism"] == "DIRECT_VA_DEPOSIT" for record in graph["pakd_trails"])
    assert any(item["institution_category"] == "PAKD" and "indodax" in item["institution_name"].lower()
               for item in graph["governance_requests"])
    retained = [record for record in graph["pakd_trails"] if record["pakd_name"] == "Indodax"
                and record["withdrawal_status"] == "STILL_IN_PAKD_WALLET"]
    assert len(retained) == 1
    assert amount(retained[0]["remaining_pakd_balance_idr"]) == 120000000


def test_cold_wallet_11000_usdt_is_external_issuer_review_not_direct_freeze(trace):
    graph = trace("p2p-dex-cold-wallet")
    assert any("uniswap" in str(node).lower() for node in graph["nodes"])
    cold = next(node for node in graph["nodes"] if node["id"] == "cold")
    assert amount(cold["balance_crypto"]) == 11000
    assert cold["chain"] == "ETHEREUM_ERC20"
    issuer = next(item for item in graph["governance_requests"] if item["target_node_id"] == cold["id"])
    assert issuer["institution_category"] == "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE"
    assert amount(issuer["requested_amount_idr"]) == 176000000
    assert issuer["request_status"] == "NOT_SENT"


@pytest.mark.parametrize("scenario_id,_", SCENARIOS)
def test_withdrawal_follows_escrow_and_precedes_onchain(trace, scenario_id, _):
    graph = trace(scenario_id)
    withdrawal = next(edge for edge in graph["edges"] if edge["edge_type"] == "EXCHANGE_WITHDRAWAL")
    withdrawal_time = datetime.fromisoformat(withdrawal["timestamp"])
    for detection in graph["p2p_detections"]:
        if detection["p2p_direction"] == "ON_RAMP_DEPOSIT":
            assert withdrawal_time >= datetime.fromisoformat(detection["escrow_release_timestamp"])
    for edge in graph["edges"]:
        if edge["edge_type"] == "ONCHAIN_TRANSFER":
            assert datetime.fromisoformat(edge["timestamp"]) >= withdrawal_time


def test_golden_period_has_full_potential_but_zero_confirmed_frozen(trace):
    graph = trace("golden-period")
    escrow = next(node for node in graph["nodes"] if node["node_type"] == "P2P_ESCROW")
    assert amount(escrow["balance_crypto"]) == 3750
    assert amount(graph["recoverable_amount_idr"]) == 95000000
    assert amount(graph["confirmed_frozen_amount_idr"]) == 0
    escrow_request = next(item for item in graph["governance_requests"] if item["target_node_id"] == escrow["id"])
    assert amount(escrow_request["requested_amount_idr"]) == 60000000
    assert escrow_request["metadata"]["request_scope"] == "TARGETED_ESCROW"


@pytest.mark.parametrize("scenario_id,_", SCENARIOS)
def test_merchant_business_is_never_target_of_blanket_account_freeze(trace, scenario_id, _):
    graph = trace(scenario_id)
    merchants = {node["id"] for node in graph["nodes"] if node["node_type"] == "P2P_MERCHANT"}
    for request in graph["governance_requests"]:
        if request["target_node_id"] in merchants:
            assert request["metadata"]["request_scope"] == "TARGETED_ESCROW"


def test_scenario_evidence_is_deterministic_and_cases_are_independent(trace):
    before = trace("p2p-full-cycle")
    trace("p2p-dex-cold-wallet")
    after = trace("p2p-full-cycle")
    for key in ["edges", "pakd_trails", "p2p_detections", "total_traced_idr", "recoverable_amount_idr", "recovery_probability_pct"]:
        assert before[key] == after[key]
    assert {item["request_id"] for item in before["governance_requests"]}.isdisjoint(
        item["request_id"] for item in after["governance_requests"])
