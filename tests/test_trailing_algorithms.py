from decimal import Decimal

import pytest

from scenarios import chain_tx, get_scenario, stamp
from schemas import IASCTransactionReportInput
from services.bank_trailer import BankTrailer, merchant_heuristic
from services.arkham_client import ArkhamClient
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge


def test_bank_branch_cannot_attribute_same_funds_twice():
    fixture = get_scenario("p2p-full-cycle")
    fixture["report"]["nominal_transaksi"] = "100"
    fixture["bank_ledger"] = [
        {"id": "first", "source": "victim", "target": "mule1", "amount_idr": "100", "timestamp": stamp()},
        {"id": "split-a", "source": "mule1", "target": "merchant", "amount_idr": "70", "timestamp": stamp(1)},
        {"id": "split-b", "source": "mule1", "target": "final-bank", "amount_idr": "70", "timestamp": stamp(2)},
        {"id": "spent-again", "source": "mule1", "target": "merchant", "amount_idr": "100", "timestamp": stamp(3)},
    ]
    nodes, edges, _ = BankTrailer().trace(IASCTransactionReportInput(**fixture["report"]), fixture)
    assert [(edge.id, edge.amount_idr) for edge in edges] == [
        ("first", Decimal(100)), ("split-a", Decimal(70)), ("split-b", Decimal(30))
    ]
    balances = {node.id: node.amount_idr for node in nodes}
    assert balances["merchant"] + balances["final-bank"] == 100
    assert sum(edge.amount_idr for edge in edges if edge.source == "mule1") == 100


def test_bank_ignores_movements_before_reported_transaction():
    fixture = get_scenario("p2p-full-cycle")
    fixture["bank_ledger"][0]["timestamp"] = stamp(-1)
    nodes, edges, candidates = BankTrailer().trace(IASCTransactionReportInput(**fixture["report"]), fixture)
    assert nodes == [] and edges == [] and candidates == []


def test_p2p_heuristic_is_explainable_and_not_an_identity_finding():
    activity = {"incoming_24h": 120, "outgoing_24h": 125, "exact_amount_ratio": "0.97"}
    result = merchant_heuristic(activity, release_delay_seconds=42)
    assert result["classification"] == "P2P_MERCHANT_CANDIDATE"
    assert result["escrow_time_correlated"] is True
    assert result["score"] > merchant_heuristic(activity)["score"]
    assert merchant_heuristic({"incoming_24h": 1, "outgoing_24h": 0})["classification"] == "INSUFFICIENT_EVIDENCE"
    assert merchant_heuristic(activity, release_delay_seconds=-1)["escrow_time_correlated"] is False
    assert merchant_heuristic(activity, release_delay_seconds=121)["escrow_time_correlated"] is False


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_amount", "wrong_account", "late_release"])
def test_p2p_bridge_rejects_ambiguous_or_inconsistent_escrow(mutation):
    fixture = get_scenario("p2p-full-cycle")
    nodes, edges, candidates = BankTrailer().trace(IASCTransactionReportInput(**fixture["report"]), fixture)
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
        P2PAndPAKDBridge().resolve_onramp(fixture, nodes, edges, candidates)


def prepare_onchain(fixture):
    nodes, edges, candidates = BankTrailer().trace(IASCTransactionReportInput(**fixture["report"]), fixture)
    P2PAndPAKDBridge().resolve_onramp(fixture, nodes, edges, candidates)
    return nodes, edges


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
        detected = P2PAndPAKDBridge().resolve_offramp(fixture, nodes, edges)
    except ValueError:
        return
    assert detected == []
