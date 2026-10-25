from datetime import datetime
from decimal import Decimal

import pytest

SCENARIOS = [
    ("double-diamond-loopback", "300000000"),
    ("multichain-dispersal", "180000000"),
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


def test_double_diamond_is_primary_complete_and_conserves_every_layer(trace, scenarios):
    assert next(iter(scenarios)) == "double-diamond-loopback"
    graph = trace("double-diamond-loopback")
    nodes = {node["id"]: node for node in graph["nodes"]}
    stages = {step: [node for node in nodes.values() if node["chronological_step"] == step] for step in range(6)}
    assert [len(stages[step]) for step in range(6)] == [1, 1, 4, 1, 4, 4]
    assert stages[0][0]["account"] == "0192837465"
    assert stages[1][0]["account"] == "8810293847"
    assert {node["account"] for node in stages[2]} == {"13000998811", "034101002233", "4120088776", "7055443322"}
    account = stages[3][0]
    assert account["metadata"]["uid"] == "TKO-9988112"
    assert account["metadata"]["kyc"]["nik_masked"] == "3171********0009"
    assert account["node_type"] == "PAKD_USER_ACCOUNT"
    assert "DIRECT VA" in graph["route_badges"] and "P2P ON-RAMP DETECTED" in graph["route_badges"]
    edges = graph["edges"]
    assert [edge["chronological_step"] for edge in edges] == sorted(edge["chronological_step"] for edge in edges)
    assert {edge["chronological_step"] for edge in edges} == set(range(1, 7))
    assert {(edge["source"], edge["target"]) for edge in edges if edge["chronological_step"] == 1} == {(stages[0][0]["id"], stages[1][0]["id"])}
    assert {amount(edge["amount_idr"]) for edge in edges if edge["chronological_step"] == 2} == {amount(80000000), amount(75000000), amount(70000000)}
    intakes = [edge for edge in edges if edge["chronological_step"] == 3]
    assert len(intakes) == 4 and {edge["target"] for edge in intakes} == {account["id"]}
    assert sum(amount(edge["amount_idr"]) for edge in intakes) == 300000000
    assert sum(amount(edge["amount_idr"]) for edge in intakes if edge["metadata"]["intake_mechanism"] == "DIRECT_VA_DEPOSIT") == 155000000
    assert sum(amount(edge["amount_idr"]) for edge in intakes if edge["metadata"]["intake_mechanism"] == "P2P_ON_RAMP_ESCROW") == 145000000
    assert {edge["metadata"]["intake_mechanism"] for edge in intakes} == {"DIRECT_VA_DEPOSIT", "P2P_ON_RAMP_ESCROW"}
    assert len(graph["p2p_detections"]) == 2
    withdrawals = [edge for edge in edges if edge["chronological_step"] == 4]
    assert sorted(amount(edge["crypto_amount"]) for edge in withdrawals) == [4500, 4500, 4750, 5000]
    assert {edge["source"] for edge in withdrawals} == {account["id"]}
    assert all(edge["asset"] == "USDT" and edge["chain"] == "ETHEREUM_ERC20" for edge in edges if edge["chronological_step"] >= 4)
    layering = [edge for edge in edges if edge["chronological_step"] == 5]
    assert len(layering) > 4
    assert all(sum(edge["source"] == node["id"] for edge in layering) >= 2 for node in stages[4])
    returns = [edge for edge in edges if edge["chronological_step"] == 6]
    assert len(returns) == 4 and {edge["target"] for edge in returns} == {account["id"]}
    for step in (4, 5, 6):
        assert sum(amount(edge["crypto_amount"]) for edge in edges if edge["chronological_step"] == step) == 18750
    for node in [*stages[4], *stages[5]]:
        assert sum(amount(edge["crypto_amount"]) for edge in edges if edge["target"] == node["id"]) == sum(amount(edge["crypto_amount"]) for edge in edges if edge["source"] == node["id"])
        assert amount(node["balance_crypto"]) == 0
    assert amount(account["balance_crypto"]) == 18750
    assert account["metadata"]["full_circle_loopback_pct"] == "100"
    assert sum(amount(node["balance_crypto"]) for node in nodes.values()) == 18750
    assert len(graph["governance_requests"]) == 1
    request = graph["governance_requests"][0]
    assert request["institution_category"] == "PAKD" and request["institution_name"] == "Tokocrypto"
    assert request["target_node_id"] == account["id"]
    assert request["request_status"] == "NOT_SENT" and amount(request["requested_amount_idr"]) == 300000000
    assert amount(graph["recoverable_amount_idr"]) == 300000000
    assert sum(amount(record["remaining_pakd_balance_idr"]) for record in graph["pakd_trails"]) == 300000000
    assert amount(graph["total_traced_usdt"]) == 18750 and amount(graph["confirmed_frozen_amount_idr"]) == 0
    assert all(item["metadata"]["chronological_step"] == item["chronological_step"] for item in [*nodes.values(), *edges])


@pytest.mark.parametrize("scenario_id,_", SCENARIOS)
def test_all_presets_supply_upstream_first_numeric_chronology(trace, scenario_id, _):
    graph = trace(scenario_id)
    assert all(type(item["chronological_step"]) is int for item in graph["nodes"] + graph["edges"])
    first = min(graph["edges"], key=lambda edge: edge["chronological_step"])
    victim = next(node for node in graph["nodes"] if node["node_type"] == "VICTIM")
    assert first["chronological_step"] == 1 and first["source"] == victim["id"]
    assert victim["chronological_step"] == 0


@pytest.mark.parametrize("field,value", [("wallet_role", "REGISTERED_WITHDRAWAL_WHITELIST"),
                                        ("user_uid", "TKO-ANOTHER"),
                                        ("kyc_nik_masked", "3171********9999")])
def test_double_diamond_only_accepts_same_custodial_uid_for_loopback(trace, field, value):
    import asyncio
    import httpx
    from scenarios import get_scenario
    from schemas import TrailNode, TrailEdge
    from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
    from services.pakd_api_consumer import PAKDAPIConsumer
    from services.pakd_scoped_mirror_provider import ScopedMirrorProvider

    graph = trace("double-diamond-loopback")
    account = next(node for node in graph["nodes"] if node["id"] == "scammer-uid")
    wallet = ScopedMirrorProvider().lookup_wallet("TOKOCRYPTO", account["address"], "ETHEREUM")
    wallet[field] = value
    consumer = PAKDAPIConsumer(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"wallet": wallet})))
    nodes = [TrailNode.model_validate(node) for node in graph["nodes"]]
    edges = [TrailEdge.model_validate(edge) for edge in graph["edges"]]
    with pytest.raises(ValueError, match="Loopback"):
        asyncio.run(P2PAndPAKDBridge(consumer).resolve_offramp(get_scenario("double-diamond-loopback"), nodes, edges))
