import pytest

from schemas import CrossRailGraphResponse
from services.recovery_action import build_evidence_packet


def issuer_target(trace, scenario_id):
    graph = CrossRailGraphResponse.model_validate(trace(scenario_id))
    request = next(item for item in graph.governance_requests
                   if item.institution_category == "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE")
    node = next(item for item in graph.nodes if item.id == request.target_node_id)
    return graph, request, node


def test_tron_tether_draft_references_privileged_function_and_canonical_contract(trace):
    graph, request, _ = issuer_target(trace, "golden-period")
    packet = build_evidence_packet(graph, request)
    issuer = packet["issuer_request"]
    assert issuer["contract_reference"] == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert issuer["function_signature"] == "addBlackList(address)"
    assert issuer["expected_event"] == "AddedBlackList(address)"
    assert issuer["scope"] == "ADDRESS_WIDE_TOKEN_RESTRICTION"
    assert packet["signed"] is False
    assert packet["transmitted"] is False
    assert "DRAFT" in packet["classification"]
    assert request.request_status == "NOT_SENT"
    assert graph.confirmed_frozen_amount_idr == 0


def test_circle_ethereum_draft_references_issuer_role_not_iasc_authority(trace):
    graph, request, node = issuer_target(trace, "p2p-dex-cold-wallet")
    node.asset = request.metadata["asset"] = "USDC"
    packet = build_evidence_packet(graph, request)
    issuer = packet["issuer_request"]
    assert issuer["issuer"] == "Circle"
    assert issuer["function_signature"] == "blacklist(address)"
    assert issuer["expected_event"] == "Blacklisted(address)"
    assert issuer["contract_reference"].lower() == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


@pytest.mark.parametrize("asset,chain", [("USDC", "TRON_TRC20"), ("USDT", "BSC_BEP20"), ("ETH", "ETHEREUM_ERC20")])
def test_unsupported_issuer_network_pair_rejected(trace, asset, chain):
    graph, request, node = issuer_target(trace, "p2p-dex-cold-wallet")
    node.asset = request.metadata["asset"] = asset
    node.chain = chain
    with pytest.raises(ValueError):
        build_evidence_packet(graph, request)
