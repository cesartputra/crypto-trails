"""Independent PAKD branches preserve fiat attribution and institution authority."""
import asyncio
from decimal import Decimal
import httpx
import pytest
from scenarios import SCENARIOS, get_scenario
from schemas import FNAMoneyTrailHandoffInput
from services.bank_trailer import FNAHandoffAdapter
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.scenario_trailer import ScenarioTrailer
from services.pakd_api_consumer import PAKDAPIConsumer, PAKDAPIError, _safe_payload
from services.pakd_scoped_mirror_provider import ScopedMirrorProvider

D = Decimal


def payload():
    s = SCENARIOS["multichain-dispersal"]["handoff"]
    return ScopedMirrorProvider().lookup_inflow("TOKOCRYPTO", s["pakd_gateway_va_or_account"], s["rrn_reference_number"], s["fiat_transfer_amount_idr"])


def test_default_catalog_has_three_allocations_and_preserves_other_presets():
    assert next(iter(SCENARIOS)) == "multichain-dispersal"
    assert len(SCENARIOS) == 7
    for scenario in SCENARIOS.values():
        assert scenario["chain_dispersals"]
    result = payload()
    assert result["chains_used"] == ["BTC", "SOLANA", "ETHEREUM"]
    assert [D(row["fiat_value_idr"]) for row in result["withdrawals"]] == [D(70000000), D(50000000), D(60000000)]
    assert sum(D(row["fiat_value_idr"]) for row in result["withdrawals"]) == D(result["inflow"]["amount_idr"])
    assert len({row["linked_inflow_id"] for row in result["withdrawals"]}) == 1


def test_default_graph_keeps_branch_units_and_eligible_money_separate(trace, client):
    graph = trace("multichain-dispersal")
    nodes = {node["id"]: node for node in graph["nodes"]}
    withdrawals = [e for e in graph["edges"] if e["edge_type"] == "EXCHANGE_WITHDRAWAL"]
    assert len(withdrawals) == 3 and {e["chain"] for e in withdrawals} == {"BTC", "SOLANA", "ETHEREUM"}
    assert {e["source"] for e in withdrawals} == {"scammer-uid"}
    assert sum(D(e["amount_idr"]) for e in withdrawals) == D(180000000)
    assert sum(D(r["fiat_in_idr"]) for r in graph["pakd_trails"] if r["withdrawal_status"] == "WITHDRAWN_ONCHAIN") == D(180000000)
    assert D(graph["total_traced_usdt"]) == D(3750)
    assert D(graph["recoverable_amount_idr"]) == D(175000000)
    assert D(graph["confirmed_frozen_amount_idr"]) == 0
    assert D(nodes["btc-deposit"]["balance_crypto"]) == D("0.065")
    assert D(nodes["sol-deposit"]["balance_crypto"]) == 0
    assert D(nodes["eth-cold"]["balance_crypto"]) == D(3750)
    assert nodes["final-sol-bank"]["entity_label"] == "BRI"
    assert D(nodes["final-sol-bank"]["amount_idr"]) == D(50000000)
    assert D(nodes["final-sol-bank"]["metadata"]["available_balance_idr"]) == D(45000000)
    eligible = {r["target_node_id"]: D(r["requested_amount_idr"]) for r in graph["governance_requests"] if D(r["requested_amount_idr"]) > 0}
    assert eligible == {"pakd-account-btc-deposit": D(70000000), "final-sol-bank": D(45000000), "eth-cold": D(60000000)}
    assert all(r["request_status"] == "NOT_SENT" for r in graph["governance_requests"])
    sell = next(e for e in graph["edges"] if e["id"] == "sol-offramp-order")
    assert sell["asset"] == "SOL" and D(sell["crypto_amount"]) == D(185)
    issuer = next(r for r in graph["governance_requests"] if r["target_node_id"] == "eth-cold")
    packet = client.post("/api/v1/governance/send-freeze-request", json={"trace_id": graph["trace_id"], "request_id": issuer["request_id"]})
    assert packet.status_code == 200
    assert packet.json()["evidence_packet"]["issuer_request"]["token"] == "USDT"


@pytest.mark.parametrize("mutation", ["wrong_family", "wrong_uid", "duplicate_id", "overallocated", "nonfinite"])
def test_consumer_rejects_unbound_or_invalid_branch_response(mutation):
    result = payload()
    row = result["withdrawals"][0]
    if mutation == "wrong_family": row["chain_family"] = "EVM"
    elif mutation == "wrong_uid": row["user_uid"] = "ANOTHER-USER"
    elif mutation == "duplicate_id": result["withdrawals"][1]["withdrawal_id"] = row["withdrawal_id"]
    elif mutation == "overallocated": row["fiat_value_idr"] = "180000001"
    else: row["crypto_amount"] = "NaN"
    consumer = PAKDAPIConsumer(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result)))
    h = SCENARIOS["multichain-dispersal"]["handoff"]
    with pytest.raises(PAKDAPIError):
        asyncio.run(consumer.consume_pakd_inflow_lookup_api(h["pakd_gateway_va_or_account"], h["rrn_reference_number"], h["fiat_transfer_amount_idr"], pakd_code="TOKOCRYPTO"))


def test_multiresponse_never_invents_single_initial_wallet_and_filters_extra_fields():
    result = payload()
    result["withdrawals"][0]["api_secret"] = "SECRET-BRANCH"
    assert "SECRET-BRANCH" not in repr(_safe_payload(result))
    async def run():
        h = SCENARIOS["multichain-dispersal"]["handoff"]
        found = await PAKDAPIConsumer().consume_pakd_inflow_lookup_api(h["pakd_gateway_va_or_account"], h["rrn_reference_number"], h["fiat_transfer_amount_idr"], pakd_code="TOKOCRYPTO")
        assert found["withdrawal_tx_hash"] is None and found["initial_wallet_address"] is None
        assert len(found["withdrawals"]) == 3
    asyncio.run(run())


def test_provider_rejects_ambiguous_fiat_inflow_but_allows_three_withdrawals():
    provider = ScopedMirrorProvider()
    original = payload()["inflow"]
    duplicate = {**original, "inflow_id": "OTHER-INFLOW-SAME-CONTEXT"}
    provider.connection("TOKOCRYPTO").execute("INSERT INTO ojk_scoped_fiat_inflows VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(duplicate.values()))
    with pytest.raises(ValueError, match="ambigu"):
        provider.lookup_inflow("TOKOCRYPTO", original["va_or_account_number"], original["rrn_reference"], original["amount_idr"])


@pytest.mark.parametrize("mutation", ["overflow", "wrong_token", "wrong_branch_total"])
def test_trace_rejects_branch_money_or_chain_inconsistency(mutation):
    s = get_scenario("multichain-dispersal")
    h = FNAMoneyTrailHandoffInput.model_validate(s["handoff"])
    async def run():
        nodes, edges = FNAHandoffAdapter().import_snapshot(h, s)
        await P2PAndPAKDBridge().resolve_onramp(h, s, nodes, edges)
        if mutation == "overflow": s["chain_ledger"][0]["crypto_amount"] = "0.066"
        elif mutation == "wrong_token": s["chain_ledger"][0]["asset"] = "USDT"
        else: s["chain_dispersals"][0]["fiat_value_idr"] = "70000001"
        with pytest.raises(ValueError): ScenarioTrailer().trace_fixture(s, nodes, edges)
    asyncio.run(run())
