import pytest


def test_manual_handoff_has_no_fabricated_ledger_or_governance_actions(client, scenarios):
    handoff = dict(scenarios["p2p-full-cycle"]["handoff"])
    handoff.pop("scenario_id", None)
    handoff["iasc_ticket_id"] = "CUSTOM-001"
    response = client.post("/api/v1/trail/trace-report", json=handoff)
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["p2p_detections"] == []
    assert graph["pakd_trails"] == []
    assert graph["governance_requests"] == []
    assert graph["warnings"]
    assert float(graph["total_traced_idr"]) == float(graph["confirmed_frozen_amount_idr"]) == 0
    assert all(node.get("address") is None for node in graph["nodes"])


@pytest.mark.parametrize("field,value", [
    ("fiat_transfer_amount_idr", "0"), ("fiat_transfer_amount_idr", "-1"),
    ("fiat_transfer_amount_idr", "NaN"), ("fiat_transfer_amount_idr", "Infinity"),
    ("fiat_transfer_amount_idr", "1000000000001"), ("fiat_transfer_amount_idr", "1.001"),
    ("fiat_transfer_timestamp", "2026-10-21T10:00:00"),
    ("victim_account_number", "<script>"), ("iasc_ticket_id", ""),
    ("fna_trail_id", ""), ("rrn_reference_number", ""),
])
def test_invalid_handoff_is_rejected(client, scenarios, field, value):
    handoff = dict(scenarios["p2p-full-cycle"]["handoff"])
    handoff[field] = value
    assert client.post("/api/v1/trail/trace-report", json=handoff).status_code == 422


@pytest.mark.parametrize("field,value", [
    ("fiat_transfer_amount_idr", "1"), ("pakd_gateway_va_or_account", "999888777000"),
    ("rrn_reference_number", "WRONG-REFERENCE"), ("last_fna_account_number", "999888777000"),
    ("fiat_transfer_timestamp", "2020-01-01T10:00:00+07:00"),
])
def test_fixture_mismatch_cannot_claim_pakd_evidence(client, scenarios, field, value):
    handoff = dict(scenarios["p2p-full-cycle"]["handoff"])
    handoff[field] = value
    response = client.post("/api/v1/trail/trace-report", json=handoff)
    assert response.status_code in {409, 422}, response.text


def test_direct_freeze_endpoint_is_retired(client):
    response = client.post("/api/v1/recovery/execute-freeze", json={"trace_id": "old", "action_id": "old"})
    assert response.status_code == 410
    assert "governance" in response.text.lower()


def test_expand_requires_case_address_and_is_repeatable(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    wallet = next(node for node in graph["nodes"] if node.get("address"))
    url = f"/api/v1/trail/address/{wallet['address']}/expand"
    params = {"trace_id": graph["trace_id"], "chain": wallet["chain"]}
    first = client.get(url, params=params)
    assert first.status_code == 200, first.text
    assert first.json()["simulation"] is True
    assert client.get(url, params=params).json() == first.json()
    assert client.get(url, params={**params, "trace_id": "unknown"}).status_code == 404
    assert client.get("/api/v1/trail/address/not-in-this-case/expand", params=params).status_code == 404


def test_expand_reveals_pool_flow_without_claiming_clean_taint_or_known_balance(client, trace):
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


def test_health_schema_and_offline_assets(client):
    assert client.get("/api/v1/health").status_code == 200
    assert "<html" in client.get("/").text.lower()
    assert client.get("/static/app.js").status_code == 200
    schema = client.get("/openapi.json").json()
    reference = schema["paths"]["/api/v1/trail/trace-report"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    required = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]["required"]
    assert {"iasc_ticket_id", "fna_trail_id", "rrn_reference_number", "pakd_gateway_va_or_account"} <= set(required)
