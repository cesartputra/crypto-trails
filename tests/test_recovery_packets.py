import pytest

from schemas import CrossRailGraphResponse
from services.recovery_action import execute_simulated_action


def issuer_target(trace, scenario_id):
    graph = CrossRailGraphResponse.model_validate(trace(scenario_id))
    action = next(item for item in graph.recovery_actions if item.action_type == "ISSUER_BLACKLIST_REQUEST")
    node = next(item for item in graph.nodes if item.id == action.node_id)
    return graph, action, node


def test_tron_tether_packet_uses_correct_official_contract(trace):
    graph, action, _ = issuer_target(trace, "golden-period")
    result = execute_simulated_action(graph, action)
    request = result["packet"]["issuer_request"]
    assert request["contract_reference"] == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert request["function_signature"] == "addBlackList(address)"
    assert request["expected_event"] == "AddedBlackList(address)"
    assert request["scope"] == "ADDRESS_WIDE_TOKEN_RESTRICTION"
    assert result["packet"]["signed"] is False
    assert result["packet"]["transmitted"] is False


def test_circle_ethereum_packet_uses_circle_privileged_function(trace):
    graph, action, node = issuer_target(trace, "p2p-dex-cold-wallet")
    node.asset = action.asset = "USDC"
    result = execute_simulated_action(graph, action)
    request = result["packet"]["issuer_request"]
    assert request["issuer"] == "Circle"
    assert request["function_signature"] == "blacklist(address)"
    assert request["expected_event"] == "Blacklisted(address)"
    assert request["contract_reference"].lower() == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


@pytest.mark.parametrize("asset,chain", [("USDC", "TRON_TRC20"), ("USDT", "BSC_BEP20"), ("ETH", "ETHEREUM_ERC20")])
def test_unsupported_issuer_network_pair_rejected(trace, asset, chain):
    graph, action, node = issuer_target(trace, "p2p-dex-cold-wallet")
    node.asset = action.asset = asset
    node.chain = chain
    with pytest.raises(ValueError):
        execute_simulated_action(graph, action)
