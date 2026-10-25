"""Account-freeze authority is bound to verified bank or internal PAKD accounts."""
from decimal import Decimal
import pytest

from schemas import CrossRailGraphResponse, SimulateInstitutionDecisionInput
from services.recovery_action import build_evidence_packet, send_request, decide_request, plan_governance_requests
from scenarios import get_scenario


def account_target(trace, scenario_id="direct-va-loopback"):
    graph = CrossRailGraphResponse.model_validate(trace(scenario_id))
    request = next(item for item in graph.governance_requests if item.institution_category == "PAKD" and item.requested_amount_idr > 0)
    node = next(item for item in graph.nodes if item.id == request.target_node_id)
    return graph, request, node


def test_pakd_packet_targets_uid_account_and_preserves_wallet_as_evidence(trace):
    graph, request, node = account_target(trace)
    packet = build_evidence_packet(graph, request)
    assert node.node_type == "PAKD_RECEIVER_ACCOUNT"
    assert packet["target"]["uid"] == request.metadata["target_uid"] == "IDX-DEMO-099"
    assert packet["target"]["account_kind"] == "PAKD_UID_ACCOUNT"
    assert packet["target"]["institution_name"] == "Indodax"
    assert "address" not in packet["target"] and "chain" not in packet["target"]
    assert "issuer_request" not in packet
    assert any(n["address"] for n in packet["evidence"]["nodes"])
    assert packet["signed"] is packet["transmitted"] is False


@pytest.mark.parametrize("kind", ["UNHOSTED_WALLET", "DEX_ROUTER", "DEX_POOL", "BRIDGE", "PUBLIC_EXCHANGE", "DIRECT_EXCHANGE_DEPOSIT", "P2P_ESCROW_DEPOSIT", "P2P_ESCROW", "P2P_MERCHANT"])
def test_labels_cannot_turn_wallet_or_escrow_into_account_freeze_target(trace, kind):
    graph, request, node = account_target(trace)
    node.node_type = kind
    node.metadata["recovery_institution"] = "PAKD"
    assert node.id not in {r.target_node_id for r in plan_governance_requests(get_scenario(graph.scenario_id), graph.nodes)}
    for action in (build_evidence_packet, send_request):
        with pytest.raises(ValueError):
            action(graph, request)
    request.request_status = "SENT_WAITING_DECISION"
    before = graph.model_dump(mode="json")
    with pytest.raises(ValueError):
        decide_request(graph, request, SimulateInstitutionDecisionInput(trace_id=graph.trace_id, request_id=request.request_id, decision="APPROVED_FREEZE_BY_INSTITUTION"))
    assert graph.model_dump(mode="json") == before


@pytest.mark.parametrize("change", ["uid", "provider", "proof_uid", "proof_provider", "proof_chain", "proof_source", "missing_proof", "category", "target_uid", "amount"])
def test_forged_account_or_scoped_identity_rejected_before_mutation(trace, change):
    graph, request, node = account_target(trace)
    if change == "uid": node.metadata["uid"] = "ATTACKER"
    elif change == "provider": node.entity_label = "Other PAKD"
    elif change == "proof_uid": node.metadata["mirror_match"]["user_uid"] = "ATTACKER"
    elif change == "proof_provider": node.metadata["mirror_match"]["pakd_name"] = "Other PAKD"
    elif change == "proof_chain": node.metadata["mirror_match"]["chain"] = "POLYGON"
    elif change == "proof_source": node.metadata["mirror_match"]["evidence_source"] = "PUBLIC_LABEL"
    elif change == "missing_proof": node.metadata.pop("mirror_match")
    elif change == "category": request.institution_category = "PUJK"
    elif change == "target_uid": request.metadata["target_uid"] = "ATTACKER"
    else: request.requested_amount_idr += 1
    before = graph.model_dump(mode="json")
    for action in (build_evidence_packet, send_request):
        with pytest.raises(ValueError): action(graph, request)
        assert graph.model_dump(mode="json") == before


def test_same_uid_accounts_aggregate_retained_inventory_once_and_dedupe_requests(trace):
    graph, _, receiver = account_target(trace, "p2p-full-cycle")
    duplicate = receiver.model_copy(deep=True)
    duplicate.id = "another-view-of-same-deposit"
    graph.nodes.append(duplicate)
    requests = plan_governance_requests(get_scenario(graph.scenario_id), graph.nodes)
    pakd = [r for r in requests if r.institution_category == "PAKD"]
    assert len(pakd) == 1
    assert pakd[0].metadata["target_uid"] == "TKR-00421"
    assert pakd[0].requested_amount_idr == Decimal("20000000")


def test_live_read_only_graph_cannot_be_used_as_automatic_freeze_evidence(trace):
    graph, request, _ = account_target(trace)
    graph.simulation = False
    graph.fna_handoff = None
    with pytest.raises(ValueError, match="read-only"):
        build_evidence_packet(graph, request)


def test_duplicate_forged_account_request_cannot_double_count_or_dispatch(trace):
    graph, request, _ = account_target(trace)
    duplicate = request.model_copy(deep=True)
    duplicate.request_id = "forged-second-account-request"
    graph.governance_requests.append(duplicate)
    before = graph.model_dump(mode="json")
    with pytest.raises(ValueError):
        send_request(graph, duplicate)
    assert graph.model_dump(mode="json") == before


def test_scoped_identity_must_still_match_the_stored_api_proof(trace):
    graph, request, node = account_target(trace)
    node.metadata["uid"] = request.metadata["target_uid"] = "FORGED-UID"
    node.metadata["mirror_match"]["user_uid"] = "FORGED-UID"
    node.metadata["mirror_match"]["scoped_wallet"]["user_uid"] = "FORGED-UID"
    before = graph.model_dump(mode="json")
    with pytest.raises(ValueError):
        send_request(graph, request)
    assert graph.model_dump(mode="json") == before


def test_golden_account_inventory_owns_only_the_matching_held_order(trace):
    graph, request, node = account_target(trace, "golden-period")
    assert node.id == "scammer-uid"
    assert node.account == "TKR-00404"
    assert node.balance_crypto == Decimal("3750")
    packet = build_evidence_packet(graph, request)
    assert packet["targeted_hold"]["maximum_hold_idr"] == "60000000"
    assert packet["targeted_hold"]["order_ids"] == ["P2P-BUY-4404"]
    assert packet["targeted_hold"]["merchant_full_account_freeze"] is False


@pytest.mark.parametrize("action", ["packet", "send", "approve"])
def test_every_aggregated_pakd_inventory_requires_its_own_stored_api_proof(trace, action):
    graph, _, receiver = account_target(trace, "p2p-full-cycle")
    forged = receiver.model_copy(deep=True)
    forged.id = "forged-extra-account-inventory"
    forged.balance_crypto = Decimal("62.5")
    forged.metadata["tainted_balance_crypto"] = "62.5"
    forged.metadata["mirror_match"]["chain"] = "ETHEREUM"
    forged.metadata["mirror_match"]["scoped_wallet"]["chain"] = "ETHEREUM"
    graph.nodes.append(forged)
    graph.governance_requests = plan_governance_requests(get_scenario(graph.scenario_id), graph.nodes)
    request = next(r for r in graph.governance_requests if r.institution_category == "PAKD")
    assert request.target_node_id == receiver.id
    assert request.requested_amount_idr == Decimal("21000000")
    if action == "approve":
        request.request_status = "SENT_WAITING_DECISION"
    before = graph.model_dump(mode="json")
    with pytest.raises(ValueError, match="API scoped"):
        if action == "packet":
            build_evidence_packet(graph, request)
        elif action == "send":
            send_request(graph, request)
        else:
            decide_request(graph, request, SimulateInstitutionDecisionInput(
                trace_id=graph.trace_id, request_id=request.request_id, decision="APPROVED_FREEZE_BY_INSTITUTION"))
    assert graph.model_dump(mode="json") == before
