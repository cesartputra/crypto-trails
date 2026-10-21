from datetime import datetime
from decimal import Decimal

import pytest


SCENARIOS = [
    ("p2p-full-cycle", "180000000"),
    ("direct-va-loopback", "120000000"),
    ("p2p-dex-cold-wallet", "250000000"),
    ("golden-period", "95000000"),
]


def amount(value):
    return Decimal(str(value))


def test_four_complete_presets(scenarios):
    assert set(scenarios) == {name for name, _ in SCENARIOS}
    required = {
        "report_id", "nama_penyelenggara_korban", "nomor_rekening_korban",
        "nama_pemilik_rekening_korban", "nama_penyelenggara_terlapor",
        "nomor_rekening_terlapor", "nama_pemilik_rekening_terlapor",
        "waktu_transaksi", "nominal_transaksi", "scenario_id",
    }
    for item in scenarios.values():
        assert required <= item["report"].keys()
        assert item["title"] and item["description"]


@pytest.mark.parametrize("scenario_id,total", SCENARIOS)
def test_graph_integrity_and_money_boundaries(trace, scenario_id, total):
    graph = trace(scenario_id)
    assert graph["simulation"] is True
    assert amount(graph["total_scam_idr"]) == amount(total)
    assert 0 <= amount(graph["recoverable_amount_idr"]) <= amount(graph["total_traced_idr"]) <= amount(total)
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
        assert 0 <= amount(node["taint_score_pct"]) <= 100
        assert amount(node["balance_crypto"]) >= 0
    for action in graph["recovery_actions"]:
        assert action["node_id"] in node_ids
    assert sum(amount(action.get("metadata", {}).get("recoverable_contribution_idr", action["amount_idr"]))
               for action in graph["recovery_actions"]) == amount(graph["recoverable_amount_idr"])


def test_full_p2p_cycle_resolves_final_bank(trace):
    graph = trace("p2p-full-cycle")
    detections = graph["p2p_detections"]
    assert {item["p2p_direction"] for item in detections} == {
        "ON_RAMP_DEPOSIT", "OFF_RAMP_WITHDRAWAL"
    }
    on_ramp = next(item for item in detections if item["p2p_direction"] == "ON_RAMP_DEPOSIT")
    off_ramp = next(item for item in detections if item["p2p_direction"] == "OFF_RAMP_WITHDRAWAL")
    assert on_ramp["merchant_id"] == "MCH-8821"
    assert amount(on_ramp["crypto_amount_usdt"]) == 11250
    assert on_ramp["order_id"] and on_ramp["scammer_uid"] and on_ramp["evidence"]
    assert off_ramp["linked_final_bank_account"] == "034101009988111"
    final = next(node for node in graph["nodes"] if node.get("account") == "034101009988111")
    assert any(action["node_id"] == final["id"] and action["action_type"] == "FREEZE_BANK"
               for action in graph["recovery_actions"])
    assert len([node for node in graph["nodes"] if node["node_zone"] == "ONCHAIN_WALLET"]) >= 4


def test_direct_va_loopback_targets_indodax(trace):
    graph = trace("direct-va-loopback")
    assert "DIRECT VA" in graph["route_badges"]
    assert graph["p2p_detections"] == []
    assert any(edge["edge_type"] == "VA_DEPOSIT" for edge in graph["edges"])
    by_id = {node["id"]: node for node in graph["nodes"]}
    actions = [action for action in graph["recovery_actions"] if action["action_type"] == "FREEZE_PAKD"]
    assert actions
    assert any("indodax" in str(by_id[action["node_id"]]).lower() for action in actions)


def test_cold_wallet_identifies_dex_and_11000_usdt(trace):
    graph = trace("p2p-dex-cold-wallet")
    assert any("uniswap" in str(node).lower() for node in graph["nodes"])
    issuer = next(action for action in graph["recovery_actions"]
                  if action["action_type"] == "ISSUER_BLACKLIST_REQUEST")
    assert issuer["asset"] == "USDT"
    assert amount(issuer["crypto_amount"]) == 11000
    cold = next(node for node in graph["nodes"] if node["id"] == issuer["node_id"])
    assert amount(cold["balance_crypto"]) == 11000
    assert cold["chain"] == "ETHEREUM_ERC20"


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


def test_golden_period_has_full_non_duplicated_recovery(trace):
    graph = trace("golden-period")
    escrow = next(node for node in graph["nodes"] if node["node_type"] == "P2P_ESCROW")
    assert amount(escrow["balance_crypto"]) == 3750
    assert amount(graph["recoverable_amount_idr"]) == 95000000
    assert amount(graph["recoverable_amount_idr"]) / amount(graph["total_scam_idr"]) * 100 == 100
    assert sum(amount(action["amount_idr"]) for action in graph["recovery_actions"]) == 95000000


@pytest.mark.parametrize("scenario_id,_", SCENARIOS)
def test_no_blanket_merchant_freeze(trace, scenario_id, _):
    graph = trace(scenario_id)
    merchant_accounts = {item["merchant_bank_account"] for item in graph["p2p_detections"]}
    merchant_ids = {node["id"] for node in graph["nodes"] if node.get("account") in merchant_accounts}
    for action in graph["recovery_actions"]:
        if action["node_id"] in merchant_ids:
            assert action["action_type"] == "TARGETED_ESCROW_HOLD"


def test_scenario_results_deterministic_and_independent(trace):
    before = trace("p2p-full-cycle")
    trace("p2p-dex-cold-wallet")
    after = trace("p2p-full-cycle")
    for key in ["nodes", "edges", "p2p_detections", "total_traced_idr", "recoverable_amount_idr", "recovery_probability_pct"]:
        assert before[key] == after[key]
