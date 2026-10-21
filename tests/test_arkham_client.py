import asyncio

import httpx
import pytest

from scenarios import get_scenario
from services.arkham_client import ArkhamAPIError, ArkhamClient


def test_no_key_uses_deterministic_data_without_http():
    def unexpected_http(request):
        pytest.fail(f"Offline demo attempted HTTP: {request.url}")

    client = ArkhamClient(api_key="", transport=httpx.MockTransport(unexpected_http))
    fixture = get_scenario("p2p-full-cycle")
    address = fixture["wallets"][0]["address"]
    first = asyncio.run(client.get_transfers(address, "TRON_TRC20"))
    second = asyncio.run(client.get_transfers(address, "TRON_TRC20"))
    assert client.simulation is True
    assert first == second and first["transfers"]
    assert first["simulation"] is True
    intelligence = asyncio.run(client.get_address_intelligence(address, "TRON_TRC20"))
    assert intelligence["simulation"] is True
    assert intelligence["type"] == "UNHOSTED_WALLET"


def test_live_read_adapter_uses_official_host_header_and_chain_names():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/transfers":
            return httpx.Response(200, json={"transfers": []})
        return httpx.Response(200, json={"address": "0x123", "entity": "Example"})

    client = ArkhamClient(api_key="test-secret", transport=httpx.MockTransport(handle))
    assert client.simulation is False
    assert asyncio.run(client.get_transfers("0x123", "ETHEREUM_ERC20")) == {"transfers": []}
    assert asyncio.run(client.get_address_intelligence("0x123", "ETHEREUM_ERC20"))["entity"] == "Example"
    assert len(requests) == 2
    assert all(request.url.scheme == "https" for request in requests)
    assert all(request.url.host == "api.arkhamintelligence.com" for request in requests)
    assert all(request.headers["API-Key"] == "test-secret" for request in requests)
    assert requests[0].url.params["base"] == "0x123"
    assert requests[0].url.params["chains"] == "ethereum"
    assert requests[1].url.params["chain"] == "ethereum"


def test_upstream_failure_does_not_silently_turn_into_simulation_or_leak_secret():
    def denied(request):
        return httpx.Response(401, json={"error": "sensitive-upstream-body"})

    client = ArkhamClient(api_key="test-secret", transport=httpx.MockTransport(denied))
    with pytest.raises(ArkhamAPIError) as error:
        asyncio.run(client.get_transfers("0x123", "ETHEREUM_ERC20"))
    assert "test-secret" not in str(error.value)
    assert "sensitive-upstream-body" not in str(error.value)
    assert client.simulation is False


@pytest.mark.parametrize("base_url", [
    "http://api.arkhamintelligence.com", "https://attacker.example", "https://key:secret@api.arkhamintelligence.com"
])
def test_api_key_cannot_be_sent_to_untrusted_base_url(base_url):
    with pytest.raises(ValueError):
        ArkhamClient(api_key="test-secret", base_url=base_url)


def test_unsupported_chain_rejected_before_http():
    client = ArkhamClient(api_key="")
    with pytest.raises(ValueError):
        asyncio.run(client.get_transfers("0x123", "UNKNOWN_CHAIN"))
