"""The real API/scanners switch provider at wallet hop three, using HTTP fixtures."""
import httpx
import pytest

from services import cryptoapis_config, etherscan_client
from services.multichain_scanner import MultichainScanner

WALLETS = ['0x' + f'{index:040x}' for index in range(1, 9)]


@pytest.fixture
def hybrid_transport(monkeypatch, tmp_path):
    calls = []
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
    assert graph['live_metadata']['cryptoapis_requests_made'] == 2 * (depth - 3)
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
