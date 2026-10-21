from decimal import Decimal
from hashlib import sha256
import json

import pytest

SEND = "/api/v1/governance/send-freeze-request"
DECIDE = "/api/v1/governance/simulate-institution-decision"
APPROVED = "APPROVED_FREEZE_BY_INSTITUTION"
PARTIAL = "PARTIAL_FREEZE_BY_INSTITUTION"
REJECTED = "REJECTED_BY_INSTITUTION"


def final_bank_request(graph):
    node = next(node for node in graph["nodes"] if node["node_type"] == "FINAL_CASHOUT")
    return next(item for item in graph["governance_requests"] if item["target_node_id"] == node["id"])


def pair(graph, request):
    return {"trace_id": graph["trace_id"], "request_id": request["request_id"]}


def sent_bank(client, trace):
    graph = trace("p2p-full-cycle")
    request = final_bank_request(graph)
    payload = pair(graph, request)
    sent = client.post(SEND, json=payload)
    assert sent.status_code == 200, sent.text
    return graph, request, payload, sent.json()


def test_dispatch_is_only_a_ticket_no_unilateral_freeze_and_replay_is_idempotent(client, trace):
    graph, request, payload, result = sent_bank(client, trace)
    assert result["simulation"] is True
    assert result["request"]["request_status"] == "SENT_WAITING_DECISION"
    assert Decimal(result["request"]["approved_frozen_amount_idr"]) == 0
    assert Decimal(result["confirmed_frozen_amount_idr"]) == 0
    assert result["request"]["decision_timestamp"] is None
    assert result["request"]["decided_by_unit"] == ""
    assert result["request"]["institution_category"] == "PUJK"
    assert result["request"]["institution_name"] == "BRI"
    assert result["evidence_packet"]
    packet_text = str(result["evidence_packet"])
    assert graph["iasc_ticket_id"] in packet_text and graph["fna_trail_id"] in packet_text
    packet = result["evidence_packet"]
    evidence = packet["evidence"]
    assert evidence["fna_handoff"]["rrn_reference_number"] == graph["fna_handoff"]["rrn_reference_number"]
    assert evidence["pakd_trails"] == graph["pakd_trails"]
    assert len(evidence["edges"]) == len(graph["edges"])
    assert {node["node_zone"] for node in evidence["nodes"]} == {"FIAT_BANK", "CRYPTO_EXCHANGE_BRIDGE", "ONCHAIN_WALLET"}
    assert packet["evidence_sha256"] == sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert client.post(SEND, json=payload).json() == result
    independent = trace("p2p-full-cycle")
    assert Decimal(independent["confirmed_frozen_amount_idr"]) == 0
    assert all(item["request_status"] == "NOT_SENT" for item in independent["governance_requests"])


def test_callback_before_dispatch_rejected(client, trace):
    graph = trace("p2p-full-cycle")
    payload = pair(graph, final_bank_request(graph))
    response = client.post(DECIDE, json={**payload, "decision": APPROVED})
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("decision,approved", [(APPROVED, "144000000"), (PARTIAL, "12000000"), (REJECTED, "0")])
def test_only_institution_decision_changes_confirmed_funds(client, trace, decision, approved):
    graph, request, payload, _ = sent_bank(client, trace)
    response = client.post(DECIDE, json={**payload, "decision": decision,
                                        "approved_frozen_amount_idr": approved,
                                        "institution_decision_notes": "Keputusan review simulasi oleh institusi tujuan."})
    assert response.status_code == 200, response.text
    result = response.json()
    actual = result["request"]
    assert result["simulation"] is True
    assert actual["request_status"] == decision
    assert actual["institution_name"] == "BRI"
    assert "BRI" in actual["decided_by_unit"] and "IASC" not in actual["decided_by_unit"]
    assert actual["decision_timestamp"]
    assert Decimal(actual["approved_frozen_amount_idr"]) == Decimal(approved)
    assert Decimal(result["confirmed_frozen_amount_idr"]) == Decimal(approved)
    assert Decimal(actual["requested_amount_idr"]) == Decimal(request["requested_amount_idr"])


def test_terminal_decision_replay_is_idempotent_conflict_rejected_and_resend_never_resets(client, trace):
    _, _, payload, sent = sent_bank(client, trace)
    callback = {**payload, "decision": PARTIAL, "approved_frozen_amount_idr": "12000000",
                "institution_decision_notes": "Saldo tersisa dua belas juta."}
    first = client.post(DECIDE, json=callback)
    assert first.status_code == 200, first.text
    assert client.post(DECIDE, json=callback).json() == first.json()
    conflicting = client.post(DECIDE, json={**callback, "approved_frozen_amount_idr": "13000000"})
    assert conflicting.status_code == 409
    resend = client.post(SEND, json=payload)
    assert resend.status_code == 200
    assert resend.json()["request"] == first.json()["request"]
    assert resend.json()["evidence_packet"] == sent["evidence_packet"]
    assert Decimal(resend.json()["confirmed_frozen_amount_idr"]) == 12000000


@pytest.mark.parametrize("decision,value", [
    (APPROVED, "144000001"), (APPROVED, "0"), (APPROVED, "1"),
    (PARTIAL, "144000000"), (PARTIAL, "144000001"), (PARTIAL, "0"),
    (PARTIAL, "-1"), (PARTIAL, "NaN"), (PARTIAL, "Infinity"),
    (PARTIAL, "1.001"), (REJECTED, "1"),
])
def test_decision_amount_must_respect_requested_and_observed_funds(client, trace, decision, value):
    _, _, payload, _ = sent_bank(client, trace)
    response = client.post(DECIDE, json={**payload, "decision": decision, "approved_frozen_amount_idr": value})
    assert response.status_code in {409, 422}, response.text


def test_client_cannot_substitute_target_institution_or_deciding_unit(client, trace):
    graph = trace("p2p-full-cycle")
    payload = pair(graph, final_bank_request(graph))
    for spoof in [{"institution_name": "Attacker"}, {"target_node_id": "victim"}, {"requested_amount_idr": "999999999"}]:
        assert client.post(SEND, json={**payload, **spoof}).status_code == 422
    assert client.post(SEND, json=payload).status_code == 200
    response = client.post(DECIDE, json={**payload, "decision": APPROVED, "decided_by_unit": "IASC"})
    assert response.status_code == 422


def test_unknown_and_other_case_request_ids_cannot_be_dispatched(client, trace):
    graph = trace("p2p-full-cycle")
    request = final_bank_request(graph)
    payload = pair(graph, request)
    assert client.post(SEND, json={**payload, "trace_id": "unknown"}).status_code == 404
    assert client.post(SEND, json={**payload, "request_id": "unknown"}).status_code == 404
    other = trace("p2p-full-cycle")
    assert client.post(SEND, json={**payload, "trace_id": other["trace_id"]}).status_code == 404


def test_multiple_institution_approvals_sum_confirmed_stocks_without_double_count(client, trace):
    graph = trace("p2p-full-cycle")
    positive = [item for item in graph["governance_requests"]
                if item["institution_category"] in {"PUJK", "PAKD"} and Decimal(item["requested_amount_idr"]) > 0]
    assert len(positive) >= 2
    total = Decimal(0)
    for request in positive:
        payload = pair(graph, request)
        assert client.post(SEND, json=payload).status_code == 200
        response = client.post(DECIDE, json={**payload, "decision": APPROVED})
        assert response.status_code == 200, response.text
        total += Decimal(request["requested_amount_idr"])
        assert Decimal(response.json()["confirmed_frozen_amount_idr"]) == total
    assert total == Decimal(graph["recoverable_amount_idr"]) == 164000000


def test_zero_balance_fna_pujk_can_reject_but_cannot_approve_funds(client, trace):
    graph = trace("p2p-full-cycle")
    zero = next(item for item in graph["governance_requests"]
                if item["institution_category"] == "PUJK" and Decimal(item["requested_amount_idr"]) == 0)
    payload = pair(graph, zero)
    assert client.post(SEND, json=payload).status_code == 200
    assert client.post(DECIDE, json={**payload, "decision": APPROVED}).status_code in {409, 422}
    rejected = client.post(DECIDE, json={**payload, "decision": REJECTED,
                                        "institution_decision_notes": "Dana telah diteruskan ke PAKD; saldo kosong."})
    assert rejected.status_code == 200, rejected.text
    assert Decimal(rejected.json()["confirmed_frozen_amount_idr"]) == 0


def test_golden_escrow_hold_is_institution_owned_and_limited_to_order(client, trace):
    graph = trace("golden-period")
    request = next(item for item in graph["governance_requests"] if item["metadata"].get("request_scope") == "TARGETED_ESCROW")
    assert request["institution_category"] == "PAKD"
    assert Decimal(request["requested_amount_idr"]) == 60000000
    payload = pair(graph, request)
    response = client.post(SEND, json=payload)
    assert response.status_code == 200, response.text
    packet = str(response.json()["evidence_packet"])
    assert "P2P-BUY-4404" in packet
    assert Decimal(response.json()["confirmed_frozen_amount_idr"]) == 0
    decision = client.post(DECIDE, json={**payload, "decision": PARTIAL, "approved_frozen_amount_idr": "30000000"})
    assert decision.status_code == 200, decision.text
    assert Decimal(decision.json()["confirmed_frozen_amount_idr"]) == 30000000


def test_projected_total_failure_is_atomic_and_keeps_request_waiting(client, trace):
    import main

    graph, request, payload, sent = sent_bank(client, trace)
    state = main.TRACES[graph["trace_id"]]
    state["graph"].recoverable_amount_idr = Decimal("1000000")
    response = client.post(DECIDE, json={**payload, "decision": APPROVED})
    assert response.status_code == 422, response.text
    replay = client.post(SEND, json=payload)
    assert replay.status_code == 200
    assert replay.json()["request"] == sent["request"]
    assert replay.json()["request"]["request_status"] == "SENT_WAITING_DECISION"
    assert replay.json()["request"]["decision_timestamp"] is None
    assert Decimal(replay.json()["confirmed_frozen_amount_idr"]) == 0


def test_failed_issuer_packet_does_not_dispatch_or_mutate_request(client, trace):
    import main

    graph = trace("p2p-dex-cold-wallet")
    request = next(item for item in graph["governance_requests"]
                   if item["institution_category"] == "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE")
    state = main.TRACES[graph["trace_id"]]
    target = next(node for node in state["graph"].nodes if node.id == request["target_node_id"])
    target.asset = "ETH"
    response = client.post(SEND, json=pair(graph, request))
    assert response.status_code == 422, response.text
    actual = next(item for item in state["graph"].governance_requests if item.request_id == request["request_id"])
    assert actual.request_status == "NOT_SENT"
    assert actual.approved_frozen_amount_idr == 0
    assert actual.decision_timestamp is None
    assert actual.request_id not in state["packets"]
    assert state["graph"].confirmed_frozen_amount_idr == 0


def test_address_wide_issuer_review_cannot_be_partial_nominal_freeze(client, trace):
    graph = trace("p2p-dex-cold-wallet")
    request = next(item for item in graph["governance_requests"]
                   if item["institution_category"] == "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE")
    payload = pair(graph, request)
    sent = client.post(SEND, json=payload)
    assert sent.status_code == 200, sent.text
    response = client.post(DECIDE, json={**payload, "decision": PARTIAL,
                                        "approved_frozen_amount_idr": "1000000"})
    assert response.status_code == 422, response.text
    replay = client.post(SEND, json=payload).json()
    assert replay["request"]["request_status"] == "SENT_WAITING_DECISION"
    assert Decimal(replay["confirmed_frozen_amount_idr"]) == 0
    assert replay["evidence_packet"]["issuer_request"]["scope"] == "ADDRESS_WIDE_TOKEN_RESTRICTION"
