"""Live explorer contracts use isolated HTTP fixtures, never production API keys."""
import asyncio
from decimal import Decimal

import httpx
import pytest

from services import etherscan_client as module
from services.etherscan_client import EtherscanAPIError, EtherscanClient, TOKEN_CONTRACTS

real_config = module._config
real_throttle = module._throttle

A, B, C = ("0x" + digit * 40 for digit in "123")
HASH = "0x" + "a" * 64


class EmptyMirror:
    def match_wallet_in_mirror_db(self, address):
        return None


@pytest.fixture(autouse=True)
def isolated_config_and_rate(monkeypatch):
    monkeypatch.setattr(module, "_config", lambda: {"ETHERSCAN_API_KEY": "", "ETHERSCAN_BASE_URL": "", "ETHERSCAN_FALLBACK_EXPLORER_URL": ""})

    async def no_delay():
        pass

    monkeypatch.setattr(module, "_throttle", no_delay)


def native(sender=A, recipient=B, **extra):
    return {"from": sender, "to": recipient, "hash": HASH, "value": "1234567890123456789",
            "timeStamp": "1700000000", "isError": "0", "txreceipt_status": "1", **extra}


def token(sender=A, recipient=B, **extra):
    return {"from": sender, "to": recipient, "hash": HASH, "value": "123456789", "timeStamp": "1700000000",
            "contractAddress": TOKEN_CONTRACTS["USDT"], "tokenSymbol": "USDT", "tokenDecimal": "6", "logIndex": "4", **extra}


def run(handler, *, api_key="test-key", **options):
    client = EtherscanClient(api_key=api_key, transport=httpx.MockTransport(handler), mirror_db=EmptyMirror())
    return asyncio.run(client.trace_real_wallet_on_etherscan(A, **options))


def explorer(handler):
    def wrapped(request):
        action = request.url.params["action"]
        result = handler(action, request)
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": result})
    return wrapped


def test_v2_real_transfers_decimal_balance_and_provenance():
    requests = []

    def handle(action, request):
        requests.append(request)
        return "2500000000000000000" if action == "balance" else [native()] if action == "txlist" else [token()]

    result = run(explorer(handle), max_hops=1)
    assert [edge.crypto_amount for edge in result["edges"]] == [Decimal("1.234567890123456789"), Decimal("123.456789")]
    assert result["nodes"][0].balance_crypto == Decimal("2.5")
    assert result["nodes"][0].asset == "ETH"
    assert result["nodes"][1].metadata["balance_status"] == "NOT_FETCHED"
    assert all(node.taint_score_pct is None and node.amount_idr == 0 for node in result["nodes"])
    assert result["metadata"]["simulation"] is False
    assert len(requests) == 3
    assert all(request.url.host == "api.etherscan.io" and request.url.params["chainid"] == "1" for request in requests)
    assert all(request.url.params["apikey"] == "test-key" for request in requests)
    assert requests[1].url.params["startblock"] == "0" and requests[1].url.params["offset"] == "15"
    assert all(edge.metadata["explorer_url"].startswith("https://etherscan.io/tx/0x") for edge in result["edges"])


def test_empty_key_uses_public_mainnet_without_sending_key():
    seen = []

    def handle(action, request):
        seen.append(request)
        return "0" if action == "balance" else [native()]

    result = run(explorer(handle), api_key="", max_hops=1, token_filter="ETH")
    assert result["metadata"]["provider"] == "BLOCKSCOUT"
    assert len(result["edges"]) == 1
    assert all(request.url.host == "eth.blockscout.com" and "apikey" not in request.url.params for request in seen)
    assert result["edges"][0].metadata["evidence_source"] == "BLOCKSCOUT_LIVE"


def test_ui_empty_key_uses_environment_key_and_v1_has_no_chainid(monkeypatch):
    monkeypatch.setattr(module, "_config", lambda: {"ETHERSCAN_API_KEY": "env-secret", "ETHERSCAN_BASE_URL": "https://api.etherscan.io/api", "ETHERSCAN_FALLBACK_EXPLORER_URL": ""})
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "1", "result": "0" if request.url.params["action"] == "balance" else []})

    instance = EtherscanClient(api_key="", transport=httpx.MockTransport(handle), mirror_db=EmptyMirror())
    asyncio.run(instance.trace_real_wallet_on_etherscan(A, max_hops=1, token_filter="ETH"))
    assert all(request.url.path == "/api" and "chainid" not in request.url.params and request.url.params["apikey"] == "env-secret" for request in seen)


def test_two_hop_edges_and_token_log_deduplication():
    def handle(action, request):
        if action == "balance":
            return "0"
        if request.url.params["address"] == A:
            return [token(), token(logIndex="5")]
        return [token(), token(logIndex="5"), token(sender=B, recipient=C, hash="0x" + "b" * 64)]

    result = run(explorer(handle), max_hops=2, token_filter="USDT")
    assert len(result["nodes"]) == 3 and len(result["edges"]) == 3
    assert [node.hop for node in result["nodes"]] == [0, 1, 2]
    assert result["metadata"]["wallets_queried"] == 2


def test_both_directions_keep_actual_edge_direction_without_duplicate():
    def handle(action, request):
        if action == "balance":
            return "0"
        incoming = native(sender=B, recipient=A)
        return [incoming] if request.url.params["address"] == A else [incoming, native(sender=C, recipient=B, hash="0x" + "b" * 64)]

    result = run(explorer(handle), max_hops=2, token_filter="ETH", direction="BOTH")
    assert len(result["edges"]) == 2
    edge = result["edges"][0]
    assert edge.source == "eth-" + B[2:] and edge.target == "eth-" + A[2:]


def test_spoof_tokens_failed_and_unrelated_transactions_skipped():
    def handle(action, request):
        if action == "balance":
            return "0"
        if action == "txlist":
            return [native(isError="1"), native(value="0"), native(sender=C), native(value="NaN"), native(to="bad")]
        return [token(contractAddress=C), token(tokenDecimal="18"), token(tokenDecimal="999"), token(), token(hash="bad")]

    result = run(explorer(handle), max_hops=1)
    assert len(result["edges"]) == 1 and result["edges"][0].asset == "USDT"
    assert any("dilewati" in warning for warning in result["warnings"])


def test_no_transactions_stays_live_without_synthetic_fallback():
    def handle(request):
        return httpx.Response(200, json={"status": "1", "result": "0"}) if request.url.params["action"] == "balance" else httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})

    result = run(handle, max_hops=1)
    assert len(result["nodes"]) == 1 and result["edges"] == []
    assert result["metadata"]["simulation"] is False


@pytest.mark.parametrize("status,payload", [
    (200, {"status": "0", "result": "Invalid test-key sensitive-upstream-body"}),
    (429, {"error": "test-key sensitive-upstream-body"}),
    (200, ["test-key"]),
])
def test_provider_errors_never_return_simulation_or_echo_key(status, payload):
    with pytest.raises(EtherscanAPIError) as error:
        run(lambda request: httpx.Response(status, json=payload), max_hops=1)
    assert "test-key" not in str(error.value) and "sensitive-upstream-body" not in str(error.value)


@pytest.mark.parametrize("base_url", ["http://api.etherscan.io/api", "https://evil.example/api", "https://api.etherscan.io@evil.example/api", "https://api.etherscan.io/api?apikey=secret", "https://api.etherscan.io/v2/api/redirect", "https://api.etherscan.io:444/v2/api"])
def test_api_keys_cannot_reach_unapproved_endpoints(base_url):
    with pytest.raises(ValueError):
        EtherscanClient(api_key="secret", base_url=base_url, mirror_db=EmptyMirror())


def test_redirects_are_rejected():
    with pytest.raises(EtherscanAPIError):
        run(lambda request: httpx.Response(302, headers={"location": "https://evil.example"}), max_hops=1)


@pytest.mark.parametrize("options", [{"max_hops": 0}, {"max_hops": 4}, {"max_hops": True}, {"token_filter": "BTC"}, {"direction": "INVALID"}])
def test_unbounded_or_unsupported_inputs_fail_before_http(options):
    with pytest.raises(ValueError):
        run(lambda request: pytest.fail("Invalid input triggered HTTP"), **options)


def test_mirror_matches_are_read_only_and_labeled_synthetic():
    class Mirror:
        def match_wallet_in_mirror_db(self, address):
            return {"pakd_name": "Indodax", "user_uid": "SIM-001", "nik_masked": "3171********0001", "simulation": True} if address == B else None

    instance = EtherscanClient(api_key="", transport=httpx.MockTransport(explorer(lambda action, request: "0" if action == "balance" else [native()])), mirror_db=Mirror())
    result = asyncio.run(instance.trace_real_wallet_on_etherscan(A, max_hops=1, token_filter="ETH"))
    match = result["nodes"][1]
    assert match.metadata["mirror_match"]["simulation"] is True
    assert match.metadata["identity_source"] == "SYNTHETIC_PAKD_MIRROR_DB_SQLITE"
    assert match.taint_score_pct is None


def test_request_budget_and_graph_limits_are_reported():
    def handle(action, request):
        if action == "balance":
            return "0"
        if action == "tokentx":
            return []
        address = request.url.params["address"]
        return [native(sender=address, recipient="0x" + f"{index:040x}", hash="0x" + f"{index:064x}") for index in range(100, 115)]

    result = run(explorer(handle), max_hops=3)
    assert result["metadata"]["requests_made"] <= 24
    assert len(result["nodes"]) <= 64 and len(result["edges"]) <= 128
    assert result["metadata"]["truncated"] is True


def test_missing_log_index_retains_distinct_identical_events_and_warns():
    def handle(action, request):
        if action == "balance":
            return "0"
        row = token()
        row.pop("logIndex")
        return [row, row]

    result = run(explorer(handle), max_hops=1, token_filter="USDT")
    assert len(result["edges"]) == 2
    assert any("logIndex" in warning for warning in result["warnings"])


def test_public_dictionary_contains_valid_addresses_and_sources():
    assert all(module.ADDRESS.fullmatch(address) for address in module.KNOWN_EVM_ENTITIES)
    assert all(entity["source_url"].startswith("https://") for entity in module.KNOWN_EVM_ENTITIES.values())


def test_dotenv_is_loaded_without_overwriting_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# local demo\nETHERSCAN_API_KEY='dotenv-demo-key'\nexport ETHERSCAN_BASE_URL=https://api.etherscan.io/api\nUNRELATED_SECRET=ignored\n")
    monkeypatch.setattr(module, "ENV_FILE", env_file)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("ETHERSCAN_BASE_URL", raising=False)
    monkeypatch.delenv("ETHERSCAN_FALLBACK_EXPLORER_URL", raising=False)
    assert real_config()["ETHERSCAN_API_KEY"] == "dotenv-demo-key"
    assert real_config()["ETHERSCAN_BASE_URL"] == "https://api.etherscan.io/api"
    assert "UNRELATED_SECRET" not in real_config()
    monkeypatch.setenv("ETHERSCAN_API_KEY", "environment-demo-key")
    assert real_config()["ETHERSCAN_API_KEY"] == "environment-demo-key"


def test_process_rate_limit_reserves_slots_across_concurrent_calls(monkeypatch):
    waits = []

    async def fake_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(module, "_next_request_at", 0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    async def concurrent():
        await asyncio.gather(real_throttle(), real_throttle(), real_throttle())

    asyncio.run(concurrent())
    assert waits == pytest.approx([0, 0.51, 1.02])
