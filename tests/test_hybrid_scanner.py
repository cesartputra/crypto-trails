"""The real API/scanners switch provider at wallet hop three, using HTTP fixtures."""
import httpx
import pytest

from services import cryptoapis_config, etherscan_client
from services.multichain_scanner import MultichainScanner

WALLETS = ['0x' + f'{index:040x}' for index in range(1, 9)]


@pytest.fixture
def hybrid_transport(monkeypatch, tmp_path, request):
    calls = []
    crypto_status = getattr(request, 'param', 200)
    monkeypatch.setattr(cryptoapis_config, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.setenv('CRYPTOAPIS_API_KEY', 'crypto-fixture-key')
    monkeypatch.setattr(etherscan_client, '_config', lambda: {'ETHERSCAN_API_KEY': 'etherscan-fixture-key',
        'ETHERSCAN_BASE_URL': '', 'ETHERSCAN_FALLBACK_EXPLORER_URL': ''})

    async def no_delay():
        pass
    monkeypatch.setattr(etherscan_client, '_throttle', no_delay)

    def handler(request):
        calls.append(request)
        if request.url.host == 'api.etherscan.io':
            address, action = request.url.params['address'], request.url.params['action']
            index = WALLETS.index(address)
            assert index < 3, 'Primary provider queried beyond wallet hop three'
            result = '0' if action == 'balance' else [{
                'from': address, 'to': WALLETS[index + 1], 'hash': '0x' + str(index + 1) * 64,
                'value': '1000000000000000000', 'timeStamp': '1700000000', 'isError': '0', 'txreceipt_status': '1'}]
            return httpx.Response(200, json={'status': '1', 'result': result})
        assert request.url.host == 'rest.cryptoapis.io'
        if crypto_status != 200:
            return httpx.Response(crypto_status, headers={'x-credits-consumed': '100',
                'x-credits-available': '0' if crypto_status == 402 else '89000', 'Retry-After': '0'})
        address, category = request.url.path.split('/')[-2:]
        index = WALLETS.index(address)
        assert 3 <= index < 6, 'Continuation restarted at seed or passed sixth hop'
        if category == 'balance':
            return httpx.Response(200, json={'data': {'item': {'confirmedBalance': {'amount': '0', 'unit': 'ETH'}}}})
        return httpx.Response(200, json={'data': {'hasMore': False, 'items': [{
            'sender': address, 'recipient': WALLETS[index + 1], 'hash': '0x' + str(index + 1) * 64,
            'value': {'amount': '1', 'unit': 'ETH'}, 'timestamp': 1700000000, 'status': '0x1'}]}})

    import main
    monkeypatch.setattr(main, 'MultichainScanner', lambda: MultichainScanner(transport=httpx.MockTransport(handler)))
    return calls


@pytest.mark.parametrize('endpoint,depth', [('live-scanner', 6), ('live-etherscan', 4)])
def test_api_merges_primary_three_then_cryptoapis_without_restarting(client, hybrid_transport, endpoint, depth):
    response = client.post('/api/v1/trail/' + endpoint, json={'wallet_address': WALLETS[0],
        'max_hops': depth, 'token_filter': 'ETH'})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert [(node['address'], node['hop']) for node in graph['nodes']] == list(zip(WALLETS[:depth + 1], range(depth + 1)))
    assert [edge['scanner_provider'] for edge in graph['edges']] == ['ETHERSCAN'] * 3 + ['CRYPTOAPIS'] * (depth - 3)
    assert graph['live_metadata']['max_hops'] == depth
    assert graph['live_metadata']['requests_made'] == len(hybrid_transport)
    assert graph['live_metadata']['cryptoapis_requests_made'] == depth - 3
    assert graph['live_metadata']['provider'] == 'ETHERSCAN, CRYPTOAPIS'
    assert all(edge['source'] in {n['id'] for n in graph['nodes']} and edge['target'] in {n['id'] for n in graph['nodes']} for edge in graph['edges'])
    assert all(node['taint_score_pct'] is None for node in graph['nodes'])
    assert graph['governance_requests'] == [] and graph['confirmed_frozen_amount_idr'] == '0'
    assert all(request.headers.get('X-API-Key') == 'crypto-fixture-key' and 'apikey' not in request.url.params
        for request in hybrid_transport if request.url.host == 'rest.cryptoapis.io')
    assert all(key not in response.text for key in ['crypto-fixture-key', 'etherscan-fixture-key'])


def test_missing_crypto_key_is_actionable_error_only_when_continuation_needed(client, hybrid_transport, monkeypatch):
    monkeypatch.setenv('CRYPTOAPIS_API_KEY', '')
    response = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0], 'token_filter': 'ETH'})
    assert response.status_code == 503 and 'CRYPTOAPIS_API_KEY' in response.json()['detail']
    assert 'nodes' not in response.json()
    assert not any(request.url.host == 'rest.cryptoapis.io' for request in hybrid_transport)
    response = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0], 'token_filter': 'ETH', 'max_hops': 3})
    assert response.status_code == 200 and len(response.json()['edges']) == 3


def test_crypto_key_is_never_accepted_from_browser_body(client, hybrid_transport):
    response = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0], 'cryptoapis_api_key': 'browser-secret'})
    assert response.status_code == 422 and 'browser-secret' not in response.text
    assert hybrid_transport == []


def test_manual_expansion_after_primary_third_hop_uses_cryptoapis(client, hybrid_transport):
    graph = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0],
        'token_filter': 'ETH', 'max_hops': 3}).json()
    response = client.get(f'/api/v1/trail/address/{WALLETS[3]}/expand', params={
        'trace_id': graph['trace_id'], 'chain': 'ETHEREUM', 'live': 'true'})
    assert response.status_code == 200, response.text
    expanded = response.json()
    assert [(node['address'], node['hop']) for node in expanded['nodes']] == [(WALLETS[4], 4)]
    assert len(expanded['edges']) == 1 and expanded['edges'][0]['scanner_provider'] == 'CRYPTOAPIS'
    from main import TRACES
    stored = TRACES[graph['trace_id']]['graph']
    assert stored.live_metadata['provider'] == 'ETHERSCAN, CRYPTOAPIS'
    assert stored.live_metadata['requests_made'] == len(hybrid_transport)
    assert stored.live_metadata['wallet_hops_traced'] == 4


def test_manual_expansion_cannot_pass_sixth_wallet_hop(client, hybrid_transport):
    graph = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0], 'token_filter': 'ETH'}).json()
    before = len(hybrid_transport)
    response = client.get(f'/api/v1/trail/address/{WALLETS[6]}/expand', params={
        'trace_id': graph['trace_id'], 'chain': 'ETHEREUM', 'live': 'true'})
    assert response.status_code == 422 and len(hybrid_transport) == before


@pytest.mark.parametrize('hybrid_transport', [429, 402, 403], indirect=True)
def test_quota_failure_returns_primary_real_graph_with_explicit_partial_status(client, hybrid_transport):
    response = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0], 'token_filter': 'ETH'})
    assert response.status_code == 200, response.text
    graph = response.json()
    metadata = graph['live_metadata']
    assert metadata['partial'] and metadata['continuation_status'] in {'RATE_LIMIT', 'CREDIT_LIMIT', 'ACCESS_LIMIT'}
    assert metadata['cryptoapis_http_status'] in {429, 402, 403}
    assert metadata['wallet_hops_traced'] == 3 and metadata['provider'] == 'ETHERSCAN'
    assert len(graph['nodes']) == 4 and len(graph['edges']) == 3
    assert metadata['requests_made'] == len(hybrid_transport)
    assert graph['simulation'] is False and graph['governance_requests'] == []
    assert graph['confirmed_frozen_amount_idr'] == '0'
    assert all(key not in response.text for key in ['crypto-fixture-key', 'etherscan-fixture-key'])


@pytest.mark.parametrize('failure', [401, 500, 'malformed-200'])
def test_fatal_manual_expansion_keeps_accounting_and_blocks_further_spend(client, hybrid_transport, monkeypatch, failure):
    import main
    graph = client.post('/api/v1/trail/live-scanner', json={'wallet_address': WALLETS[0],
        'token_filter': 'ETH', 'max_hops': 3}).json()
    stored = main.TRACES[graph['trace_id']]['graph']
    stored.live_metadata.update(cryptoapis_requests_made=4, cryptoapis_credits_consumed=568,
        cryptoapis_credits_estimated=0, token_filter='ALL')
    stored.live_metadata['requests_made'] += 4
    requests_before = stored.live_metadata['requests_made']
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/transactions'):
            return httpx.Response(200, headers={'x-credits-consumed': '142', 'x-credits-available': '88100'},
                json={'data': {'hasMore': False, 'items': [{
                    'sender': WALLETS[3], 'recipient': WALLETS[4], 'hash': '0x' + '4' * 64,
                    'value': {'amount': '1', 'unit': 'ETH'}, 'timestamp': 1700000000, 'status': '0x1'}]}})
        assert request.url.path.endswith('/tokens-transfers')
        return httpx.Response(200 if failure == 'malformed-200' else failure,
            headers={'x-credits-consumed': '100', 'x-credits-available': '88000'},
            text='private-upstream-secret')

    monkeypatch.setattr(main, 'MultichainScanner', lambda: MultichainScanner(transport=httpx.MockTransport(handler)))
    url = f'/api/v1/trail/address/{WALLETS[3]}/expand'
    params = {'trace_id': graph['trace_id'], 'chain': 'ETHEREUM', 'live': 'true'}
    response = client.get(url, params=params)
    assert response.status_code == 502, response.text
    assert all(secret not in response.text for secret in ['private-upstream-secret', 'crypto-fixture-key', 'etherscan-fixture-key'])
    assert len(calls) == 2
    assert stored.live_metadata['cryptoapis_requests_made'] == 6
    assert stored.live_metadata['cryptoapis_credits_consumed'] == 810
    assert stored.live_metadata['cryptoapis_credits_available'] == 88000
    assert stored.live_metadata['requests_made'] == requests_before + 2
    assert len(stored.nodes) == 4 and len(stored.edges) == 3

    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    assert response.json()['metadata']['partial']
    assert response.json()['metadata']['continuation_status'] == 'BUDGET_LIMIT'
    assert len(calls) == 2
    assert stored.live_metadata['cryptoapis_requests_made'] == 6
    assert stored.live_metadata['cryptoapis_credits_consumed'] == 810
