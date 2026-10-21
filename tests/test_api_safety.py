import json

import pytest


def test_custom_report_returns_data_gap(client, scenarios):
    report = dict(scenarios["p2p-full-cycle"]["report"])
    report.pop("scenario_id", None)
    report["report_id"] = "CUSTOM-001"
    report["nomor_rekening_terlapor"] = "999888777000"
    response = client.post("/api/v1/trail/trace-report", json=report)
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["p2p_detections"] == []
    assert graph["recovery_actions"] == []
    assert graph["warnings"]
    assert all(node["node_zone"] == "FIAT_BANK" for node in graph["nodes"])


@pytest.mark.parametrize("field,value", [
    ("nominal_transaksi", "0"),
    ("nominal_transaksi", "-1"),
    ("nominal_transaksi", "NaN"),
    ("nominal_transaksi", "Infinity"),
    ("nominal_transaksi", "1000000000001"),
    ("nominal_transaksi", "1.001"),
    ("waktu_transaksi", "2026-10-21T10:00:00"),
    ("nomor_rekening_korban", "<script>"),
    ("report_id", ""),
])
def test_invalid_report_rejected(client, scenarios, field, value):
    report = dict(scenarios["p2p-full-cycle"]["report"])
    report[field] = value
    assert client.post("/api/v1/trail/trace-report", json=report).status_code == 422


@pytest.mark.parametrize("field,value", [
    ("nominal_transaksi", "1"),
    ("nomor_rekening_terlapor", "999888777000"),
    ("waktu_transaksi", "2020-01-01T10:00:00+07:00"),
])
def test_fixture_mismatch_cannot_claim_demo_evidence(client, scenarios, field, value):
    report = dict(scenarios["p2p-full-cycle"]["report"])
    report[field] = value
    response = client.post("/api/v1/trail/trace-report", json=report)
    assert response.status_code in {409, 422}, response.text


def test_recovery_is_server_scoped_and_idempotent(client, trace):
    graph = trace("p2p-full-cycle")
    action = next(item for item in graph["recovery_actions"] if item["action_type"] == "FREEZE_BANK")
    payload = {"trace_id": graph["trace_id"], "action_id": action["id"]}
    first = client.post("/api/v1/recovery/execute-freeze", json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["simulation"] is True
    assert result["action_id"] == action["id"]
    assert result["node_id"] == action["node_id"]
    second = client.post("/api/v1/recovery/execute-freeze", json=payload)
    assert second.status_code == 200
    assert second.json() == result
    other = trace("p2p-full-cycle")
    assert all(node["status"] == "TRACED" for node in other["nodes"])
    assert all(item["status"] == "READY" for item in other["recovery_actions"])
    tampered = dict(payload, amount_idr="999999999999", target_account="1234")
    assert client.post("/api/v1/recovery/execute-freeze", json=tampered).status_code == 422
    assert client.post("/api/v1/recovery/execute-freeze", json={**payload, "action_id": "not-authorized"}).status_code == 404
    assert client.post("/api/v1/recovery/execute-freeze", json={**payload, "trace_id": "missing"}).status_code == 404
    unrelated = trace("p2p-dex-cold-wallet")
    assert client.post("/api/v1/recovery/execute-freeze", json={**payload, "trace_id": unrelated["trace_id"]}).status_code == 404


def test_issuer_packet_is_draft_and_does_not_call_event(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    action = next(item for item in graph["recovery_actions"] if item["action_type"] == "ISSUER_BLACKLIST_REQUEST")
    response = client.post("/api/v1/recovery/execute-freeze", json={
        "trace_id": graph["trace_id"], "action_id": action["id"]
    })
    assert response.status_code == 200, response.text
    result = response.json()
    packet = json.dumps(result["packet"])
    assert result["simulation"] is True
    assert "DRAFT" in packet
    assert "addBlackList" in packet
    assert "AddedBlackList" in packet
    assert "private_key" not in packet


def test_expand_requires_trace_ownership_and_is_repeatable(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    wallet = next(node for node in graph["nodes"] if node["node_zone"] == "ONCHAIN_WALLET" and node.get("address"))
    url = f"/api/v1/trail/address/{wallet['address']}/expand"
    params = {"trace_id": graph["trace_id"], "chain": wallet["chain"]}
    first = client.get(url, params=params)
    assert first.status_code == 200, first.text
    expanded = first.json()
    assert "nodes" in expanded and "edges" in expanded
    assert expanded["simulation"] is True
    again = client.get(url, params=params)
    assert again.status_code == 200
    assert again.json() == expanded
    assert client.get(url, params={**params, "trace_id": "unknown"}).status_code == 404
    assert client.get("/api/v1/trail/address/not-in-this-case/expand", params=params).status_code == 404


def test_expand_reveals_known_pool_hop_without_claiming_taint(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    dex = next(node for node in graph["nodes"] if node["node_type"] == "DEX_ROUTER")
    response = client.get(f"/api/v1/trail/address/{dex['address']}/expand", params={
        "trace_id": graph["trace_id"], "chain": dex["chain"]
    })
    assert response.status_code == 200, response.text
    expanded = response.json()
    assert expanded["nodes"] and expanded["edges"]
    known = {node["id"] for node in graph["nodes"]}
    assert any(node["id"] not in known for node in expanded["nodes"])
    assert all(node["taint_score_pct"] is None for node in expanded["nodes"])
    assert all(node["metadata"]["balance_status"] == "NOT_FETCHED" for node in expanded["nodes"])
    assert expanded["warnings"]


def test_health_and_offline_assets(client):
    assert client.get("/api/v1/health").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "<html" in page.text.lower()
