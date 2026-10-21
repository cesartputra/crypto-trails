from copy import deepcopy


def mock_live_provider(monkeypatch, transfers):
    from services.arkham_client import ArkhamClient

    monkeypatch.setenv("ARKHAM_API_KEY", "test-secret")

    async def get_transfers(self, address, chain):
        return {"transfers": deepcopy(transfers)}

    async def get_intelligence(self, address, chain):
        return {"address": address, "entity": "Mock provider"}

    monkeypatch.setattr(ArkhamClient, "get_transfers", get_transfers)
    monkeypatch.setattr(ArkhamClient, "get_address_intelligence", get_intelligence)


def transfer(source, target="0x" + "a" * 40):
    return {"fromAddress": {"address": source}, "toAddress": {"address": target},
            "tokenSymbol": "USDT", "unitValue": "10", "chain": "ethereum",
            "blockTimestamp": "2026-10-21T09:04:00+07:00", "transactionHash": "0x" + "ab" * 32}


def test_explicit_live_request_without_key_does_not_silently_simulate(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    wallet = next(node for node in graph["nodes"] if node.get("address"))
    response = client.get(f"/api/v1/trail/address/{wallet['address']}/expand", params={
        "trace_id": graph["trace_id"], "chain": wallet["chain"], "live": "true"
    })
    assert response.status_code == 409


def test_live_evm_source_address_case_is_normalized_and_taint_stays_unknown(client, trace, monkeypatch):
    graph = trace("p2p-dex-cold-wallet")
    wallet = next(node for node in graph["nodes"] if (node.get("address") or "").startswith("0x88B"))
    mock_live_provider(monkeypatch, [transfer(wallet["address"].lower())])
    response = client.get(f"/api/v1/trail/address/{wallet['address']}/expand", params={
        "trace_id": graph["trace_id"], "chain": wallet["chain"], "live": "true"
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["simulation"] is False
    assert len(result["nodes"]) == len(result["edges"]) == 1
    assert result["nodes"][0]["taint_score_pct"] is None
    assert result["nodes"][0]["metadata"]["taint_status"] == "UNKNOWN"
    assert result["warnings"]


def test_malformed_live_transfer_does_not_create_phantom_node(client, trace, monkeypatch):
    graph = trace("p2p-dex-cold-wallet")
    wallet = next(node for node in graph["nodes"] if (node.get("address") or "").startswith("0x88B"))
    good = transfer(wallet["address"])
    missing_hash = transfer(wallet["address"], "0x" + "b" * 40)
    missing_hash.pop("transactionHash")
    negative = transfer(wallet["address"], "0x" + "c" * 40)
    negative["unitValue"] = "-10"
    wrong_chain = transfer(wallet["address"], "0x" + "d" * 40)
    wrong_chain["chain"] = "bsc"
    naive_timestamp = transfer(wallet["address"], "0x" + "e" * 40)
    naive_timestamp["blockTimestamp"] = "2026-10-21T09:04:00"
    mock_live_provider(monkeypatch, [good, missing_hash, negative, wrong_chain, naive_timestamp])
    response = client.get(f"/api/v1/trail/address/{wallet['address']}/expand", params={
        "trace_id": graph["trace_id"], "chain": wallet["chain"], "live": "true"
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["nodes"]) == len(result["edges"]) == 1
    assert result["nodes"][0]["address"] == good["toAddress"]["address"]
