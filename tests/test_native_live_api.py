"""Unified API keeps native-chain evidence read-only and request secrets private."""
import pytest

from schemas import TrailNode
from services.multichain_scanner import MultichainScanner, ScannerAPIError

BTC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
SOL = "So11111111111111111111111111111111111111112"


@pytest.mark.parametrize("chain,address", [("BTC", BTC.upper()), ("SOLANA", SOL)])
def test_native_endpoint_origin_and_expand_do_not_claim_recovery(client, monkeypatch, chain, address):
    calls = []

    async def scanner(self, wallet_address, source_chain, **kwargs):
        calls.append((wallet_address, source_chain))
        return {"nodes": [TrailNode(id="native-seed", label="Public account", address=wallet_address,
            chain=source_chain, node_zone="ONCHAIN_WALLET", node_type="LIVE_UNATTRIBUTED_WALLET")],
            "edges": [], "warnings": [], "metadata": {"provider": "PUBLIC_SCANNER"}}

    monkeypatch.setattr(MultichainScanner, "trace_wallet", scanner)
    response = client.post("/api/v1/trail/live-scanner", json={"wallet_address": address,
        "source_chain": chain, "simulate_fna_and_mirror_origin": True, "etherscan_api_key": "never-echo"})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["simulation"] is False
    assert graph["governance_requests"] == []
    assert graph["recoverable_amount_idr"] == graph["confirmed_frozen_amount_idr"] == "0"
    assert len(graph["edges"]) == 2 and all(edge["tx_hash"] is None for edge in graph["edges"])
    assert "never-echo" not in response.text
    expected = address.lower() if chain == "BTC" else address
    assert calls == [(expected, chain)]
    expanded = client.get(f"/api/v1/trail/address/{address}/expand",
        params={"trace_id": graph["trace_id"], "chain": chain, "live": "true"})
    assert expanded.status_code == 200, expanded.text
    assert expanded.json()["nodes"] == []
    if chain == "SOLANA":
        assert client.get(f"/api/v1/trail/address/{address.lower()}/expand",
            params={"trace_id": graph["trace_id"], "chain": chain}).status_code == 404


@pytest.mark.parametrize("chain,address,token", [("BTC", SOL, "ALL"), ("SOLANA", BTC, "ALL"),
    ("BTC", BTC, "USDT"), ("SOLANA", SOL, "BTC")])
def test_native_chain_validation_happens_before_network(client, monkeypatch, chain, address, token):
    async def unexpected(*args, **kwargs):
        pytest.fail("Invalid native request reached public scanner")
    monkeypatch.setattr(MultichainScanner, "trace_wallet", unexpected)
    response = client.post("/api/v1/trail/live-scanner", json={"wallet_address": address,
        "source_chain": chain, "token_filter": token, "etherscan_api_key": "private-input"})
    assert response.status_code == 422
    assert "private-input" not in response.text


def test_native_failure_does_not_fall_back_to_scenario(client, monkeypatch):
    async def failed(*args, **kwargs):
        raise ScannerAPIError("Provider tidak tersedia; ulangi nanti")
    monkeypatch.setattr(MultichainScanner, "trace_wallet", failed)
    response = client.post("/api/v1/trail/live-scanner", json={"wallet_address": BTC, "source_chain": "BTC"})
    assert response.status_code == 502 and "nodes" not in response.json()
