"""Solscan credentials stay on the official API and native SOL evidence stays exact."""
import asyncio
from decimal import Decimal
import json

import httpx
import pytest

from services import etherscan_client, multichain_scanner as module
from services.chains import BASE58
from services.multichain_scanner import MultichainScanner, ScannerAPIError, SOLSCAN_BASE_URL, SOLSCAN_NATIVE_TOKEN

SOL = 'So11111111111111111111111111111111111111112'
OTHER = '11111111111111111111111111111111'
KEY = 'solscan-private-test-key'


def signature(byte=1):
    number, result = int.from_bytes(bytes([byte]) * 64, 'big'), ''
    while number:
        number, remainder = divmod(number, 58)
        result = BASE58[remainder] + result
    return result


class Consumer:
    logs = []

    async def consume_pakd_wallet_reverse_lookup_api(self, address, chain):
        return None


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(etherscan_client, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.setenv('SOLSCAN_API_KEY', KEY)
    monkeypatch.setenv('SOLSCAN_BASE_URL', SOLSCAN_BASE_URL)
    async def immediate():
        pass
    monkeypatch.setattr(module, '_throttle', immediate)


def row(**kwargs):
    return {'trans_id': signature(), 'from_address': SOL, 'to_address': OTHER, 'amount': '123456789',
        'token_address': SOLSCAN_NATIVE_TOKEN, 'token_decimals': 9, 'block_time': 1700000000,
        'activity_type': 'ACTIVITY_SPL_TRANSFER', **kwargs}


def run(rows=None, handler=None, **kwargs):
    calls = []
    def default(request):
        calls.append(request)
        if handler:
            return handler(request)
        data = {'account': SOL, 'lamports': 1234567890} if request.url.path.endswith('/detail') else rows or []
        return httpx.Response(200, json={'success': True, 'data': data})
    scanner = MultichainScanner(transport=httpx.MockTransport(default), pakd_consumer=Consumer())
    return asyncio.run(scanner.trace_wallet(SOL, 'SOLANA', max_hops=1, **kwargs)), calls


def test_key_routes_to_solscan_with_header_only_native_filter_and_exact_units():
    graph, calls = run([row()], etherscan_api_key='must-not-reach-solscan')
    assert len(calls) == 2 and all(request.url.host == 'pro-api.solscan.io' for request in calls)
    assert all(request.headers['token'] == KEY and 'authorization' not in request.headers for request in calls)
    assert all(KEY not in str(request.url) + request.content.decode() for request in calls)
    assert all('must-not-reach-solscan' not in str(request.headers) for request in calls)
    assert calls[1].url.params['token'] == SOLSCAN_NATIVE_TOKEN
    assert calls[1].url.params['flow'] == 'out' and calls[1].url.params['activity_type[]'] == 'ACTIVITY_SPL_TRANSFER'
    assert calls[1].url.params['page_size'] == '10' and calls[1].url.params['sort_order'] == 'desc'
    assert graph['metadata']['provider'] == 'SOLSCAN_PRO_API'
    assert len(graph['edges']) == 1 and graph['edges'][0].crypto_amount == Decimal('0.123456789')
    assert graph['edges'][0].amount_idr == 0 and graph['edges'][0].tx_hash == signature()
    assert all(item.scanner_provider == 'SOLSCAN_PRO_API' for item in graph['nodes'] + graph['edges'])
    assert all(node.taint_score_pct is None for node in graph['nodes'])
    assert graph['nodes'][0].balance_crypto == Decimal('1.234567890')
    assert KEY not in repr(graph)


def test_dotenv_key_and_environment_precedence(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text(f'SOLSCAN_API_KEY="dotenv-key"\nSOLSCAN_BASE_URL={SOLSCAN_BASE_URL}\n')
    monkeypatch.setattr(etherscan_client, 'ENV_FILE', path)
    monkeypatch.delenv('SOLSCAN_API_KEY')
    graph, calls = run([row()])
    assert graph['edges'] and calls[0].headers['token'] == 'dotenv-key'
    monkeypatch.setenv('SOLSCAN_API_KEY', KEY)
    assert run([row()])[1][0].headers['token'] == KEY


def test_blank_key_uses_rpc_without_solscan_headers(monkeypatch):
    monkeypatch.setenv('SOLSCAN_API_KEY', '')
    def handler(request):
        assert str(request.url) == module.SOLANA_RPC and 'token' not in request.headers
        body = json.loads(request.content)
        result = {'value': 0} if body['method'] == 'getBalance' else []
        return httpx.Response(200, json={'result': result})
    graph, calls = run(handler=handler)
    assert graph['metadata']['provider'] == 'SOLANA_MAINNET_RPC' and len(calls) == 2


@pytest.mark.parametrize('url', ['https://evil.example/v2.0', 'https://pro-api.solscan.io.evil.example/v2.0',
    'http://pro-api.solscan.io/v2.0', 'https://user@pro-api.solscan.io/v2.0',
    SOLSCAN_BASE_URL + '?token=secret', SOLSCAN_BASE_URL + '#part', SOLSCAN_BASE_URL + '/other'])
def test_invalid_url_fails_before_any_network(monkeypatch, url):
    monkeypatch.setenv('SOLSCAN_BASE_URL', url)
    with pytest.raises(ScannerAPIError, match='SOLSCAN_BASE_URL'):
        run(handler=lambda request: pytest.fail('Invalid URL reached network'))


@pytest.mark.parametrize('key', ['secret\nvalue', 'bad key', 'x' * 2049, 'unicode-é'])
def test_invalid_key_fails_before_network_without_echo(monkeypatch, key):
    monkeypatch.setenv('SOLSCAN_API_KEY', key)
    with pytest.raises(ScannerAPIError) as error:
        run(handler=lambda request: pytest.fail('Invalid key reached network'))
    assert key not in str(error.value)


@pytest.mark.parametrize('status,expected', [(401, 'autentikasi'), (403, 'akses ditolak'), (429, 'kuota'), (500, 'gagal diakses')])
def test_solscan_errors_are_specific_safe_and_do_not_fall_back_to_rpc(status, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=KEY + ' secret provider body')
    with pytest.raises(ScannerAPIError) as error:
        run(handler=handler)
    assert expected in str(error.value) and 'Solscan Pro API' in str(error.value)
    assert KEY not in str(error.value) and 'secret provider body' not in str(error.value)
    assert len(calls) == 1


def test_solscan_connection_failure_is_safe_and_names_provider():
    def handler(request):
        raise httpx.ConnectError(KEY + ' connection details', request=request)
    with pytest.raises(ScannerAPIError) as error:
        run(handler=handler)
    assert 'Solscan Pro API' in str(error.value) and KEY not in str(error.value)


def test_redirect_does_not_send_key_to_another_host():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://evil.example/'})
    with pytest.raises(ScannerAPIError):
        run(handler=handler)
    assert len(calls) == 1 and calls[0].url.host == 'pro-api.solscan.io'


@pytest.mark.parametrize('data', [{'success': False, 'errors': {'message': KEY}}, {'success': True}, 'invalid'])
def test_invalid_envelope_is_not_accepted(data):
    with pytest.raises(ScannerAPIError) as error:
        run(handler=lambda request: httpx.Response(200, json=data))
    assert KEY not in str(error.value)


@pytest.mark.parametrize('changes', [{'token_address': SOL}, {'token_decimals': 8}, {'amount': '-1'},
    {'amount': True}, {'amount': 'NaN'}, {'amount': '1.2'}, {'amount': 2**64}, {'trans_id': 'invalid'},
    {'from_address': OTHER, 'to_address': SOL}, {'activity_type': 'ACTIVITY_SPL_MINT'},
    {'block_time': 999999999999}, {'from_address': 'invalid'}])
def test_invalid_or_unrelated_transfer_does_not_become_native_sol_edge(changes):
    graph, _ = run([row(**changes)])
    assert graph['edges'] == []


def test_both_keeps_inbound_and_multiple_same_transaction_transfers():
    rows = [row(), row(amount=100), row(), row(from_address=OTHER, to_address=SOL, amount=200)]
    graph, calls = run(rows, direction='BOTH')
    assert 'flow' not in calls[1].url.params
    assert len(graph['edges']) == 4 and len({edge.id for edge in graph['edges']}) == 4
    assert len(graph['metadata']['activity']) == 1


def test_scientific_json_integer_keeps_exact_lamports_and_huge_exponents_are_rejected():
    def handler(request):
        data = {'account': SOL, 'lamports': 0} if request.url.path.endswith('/detail') else [row(amount=0), row(amount=0)]
        body = json.dumps({'success': True, 'data': data})
        if isinstance(data, list):
            body = body.replace('"amount": 0', '"amount": 123456789e0', 1).replace('"amount": 0', '"amount": 1e10000000', 1)
        return httpx.Response(200, text=body)
    graph, _ = run(handler=handler)
    assert len(graph['edges']) == 1 and graph['edges'][0].crypto_amount == Decimal('0.123456789')


def test_multihop_repeated_transfer_is_not_counted_twice():
    def handler(request):
        data = {'account': SOL, 'lamports': 0} if request.url.path.endswith('/detail') else [row()]
        return httpx.Response(200, json={'success': True, 'data': data})
    scanner = MultichainScanner(transport=httpx.MockTransport(handler), pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet(SOL, 'SOLANA', max_hops=2, direction='BOTH'))
    assert len(graph['edges']) == 1 and graph['metadata']['requests_made'] == 3


def test_scientific_integer_balance_is_exact():
    def handler(request):
        body = '{"success":true,"data":{"account":"' + SOL + '","lamports":123456789e1}}' if request.url.path.endswith('/detail') else '{"success":true,"data":[]}'
        return httpx.Response(200, text=body)
    graph, _ = run(handler=handler)
    assert graph['nodes'][0].balance_crypto == Decimal('1.234567890')


def test_solscan_key_is_not_sent_to_bitcoin():
    def handler(request):
        assert request.url.host == 'mempool.space'
        assert 'token' not in request.headers and KEY not in str(request.url)
        data = [] if request.url.path.endswith('/txs') else {'chain_stats': {'funded_txo_sum': 0, 'spent_txo_sum': 0}, 'mempool_stats': {'funded_txo_sum': 0, 'spent_txo_sum': 0}}
        return httpx.Response(200, json=data)
    scanner = MultichainScanner(transport=httpx.MockTransport(handler), pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet('bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh', 'BTC', max_hops=1))
    assert graph['metadata']['provider'] == 'MEMPOOL_SPACE'


@pytest.mark.parametrize('status', [200, 401])
def test_live_endpoint_uses_solscan_and_never_exposes_credentials(client, monkeypatch, status):
    import main
    def handler(request):
        assert request.headers['token'] == KEY
        data = {'account': SOL, 'lamports': 0} if request.url.path.endswith('/detail') else [row()]
        return httpx.Response(status, json={'success': True, 'data': data, 'unexpected_secret': KEY})
    monkeypatch.setattr(main, 'MultichainScanner', lambda: MultichainScanner(transport=httpx.MockTransport(handler), pakd_consumer=Consumer()))
    response = client.post('/api/v1/trail/live-scanner', json={'source_chain': 'SOLANA', 'wallet_address': SOL, 'max_hops': 1})
    assert KEY not in response.text
    if status == 401:
        assert response.status_code == 502 and 'HTTP 401' in response.json()['detail']
        assert 'nodes' not in response.json()
    else:
        assert response.status_code == 200, response.text
        graph = response.json()
        assert graph['live_metadata']['provider'] == 'SOLSCAN_PRO_API'
        assert graph['governance_requests'] == [] and graph['recoverable_amount_idr'] == '0'
        assert KEY not in repr(main.TRACES[graph['trace_id']])


def test_health_reports_configured_solana_provider_without_secret(client, monkeypatch):
    response = client.get('/api/v1/health')
    assert response.json()['dedicated_scanners']['SOLANA'] == 'SOLSCAN_PRO_API'
    assert KEY not in response.text
    monkeypatch.setenv('SOLSCAN_API_KEY', '')
    assert client.get('/api/v1/health').json()['dedicated_scanners']['SOLANA'] == 'SOLANA_MAINNET_RPC'
