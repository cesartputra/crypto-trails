from copy import deepcopy
from decimal import Decimal
import pytest
from schemas import TrailNode, TrailEdge
from services.etherscan_client import EtherscanClient, EtherscanAPIError

A = '0x' + 'a' * 40
B = '0x' + 'b' * 40
TX = '0x' + '12' * 32


def mock_engine(monkeypatch):
    calls = []
    async def engine(self, seed_wallet_address, max_hops=2, token_filter='ALL', direction='OUTBOUND', source_chain='ETHEREUM', auto_scan_cross_chain=False):
        calls.append((seed_wallet_address, max_hops, token_filter, direction, self.api_key))
        return {'nodes': [
            TrailNode(id='seed', label='Seed', node_zone='ONCHAIN_WALLET', node_type='LIVE_WALLET', address=seed_wallet_address,
                      chain='ETHEREUM_ERC20', metadata={'evidence_source': 'ETHERSCAN_LIVE', 'is_live': True, 'taint_status': 'UNKNOWN'}),
            TrailNode(id='counterparty', label='Counterparty', node_zone='ONCHAIN_WALLET', node_type='LIVE_WALLET', address=B,
                      chain='ETHEREUM_ERC20', hop=1, metadata={'evidence_source': 'ETHERSCAN_LIVE', 'is_live': True, 'taint_status': 'UNKNOWN'})],
            'edges': [TrailEdge(id='tx1', source='seed', target='counterparty', edge_type='ONCHAIN_TRANSFER', label='1 ETH',
                      crypto_amount=Decimal(1), asset='ETH', tx_hash=TX, timestamp='2026-10-22T00:00:00+00:00',
                      metadata={'evidence_source': 'ETHERSCAN_LIVE', 'is_live': True})],
            'warnings': ['Live read-only, kepemilikan belum diverifikasi.'],
            'metadata': {'provider': 'ETHERSCAN', 'seed_wallet_address': seed_wallet_address}}
    monkeypatch.setattr(EtherscanClient, 'trace_real_wallet_on_etherscan', engine)
    return calls


def test_live_endpoint_uses_requested_key_without_saving_or_claiming_recovery(client, monkeypatch):
    calls = mock_engine(monkeypatch)
    response = client.post('/api/v1/trail/live-etherscan', json={
        'wallet_address': A, 'etherscan_api_key': 'ui-secret', 'max_hops': 1, 'token_filter': 'ETH'})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert calls == [(A, 1, 'ETH', 'OUTBOUND', 'ui-secret')]
    assert graph['simulation'] is False and graph['fna_handoff'] is None
    assert len(graph['nodes']) == 2 and graph['governance_requests'] == []
    assert graph['pakd_trails'] == []
    assert Decimal(graph['recoverable_amount_idr']) == Decimal(graph['confirmed_frozen_amount_idr']) == 0
    assert all(node['taint_score_pct'] is None for node in graph['nodes'])
    assert 'ui-secret' not in response.text
    from main import TRACES
    assert 'ui-secret' not in repr(TRACES[graph['trace_id']])


def test_live_simulated_origin_is_labeled_and_does_not_insert_fake_mirror_wallet(client, monkeypatch):
    mock_engine(monkeypatch)
    before = client.get('/api/v1/mirror-db/records').json()
    response = client.post('/api/v1/trail/live-etherscan', json={
        'wallet_address': A, 'simulate_fna_and_mirror_origin': True})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph['simulation'] is False and graph['live_metadata']['simulated_origin'] is True
    assert {node['node_zone'] for node in graph['nodes']} == {'FIAT_BANK','CRYPTO_EXCHANGE_BRIDGE','ONCHAIN_WALLET'}
    synthetic = [edge for edge in graph['edges'] if edge['edge_type'].startswith('SIMULATED_')]
    assert len(synthetic) == 2 and all(edge['tx_hash'] is None for edge in synthetic)
    assert all(edge['metadata']['simulation'] is True for edge in synthetic)
    assert client.get('/api/v1/mirror-db/records').json() == before


@pytest.mark.parametrize('field,value', [
    ('wallet_address','0x123'), ('wallet_address','https://bad.example'), ('max_hops',0), ('max_hops',3),
    ('max_hops',True), ('token_filter','BTC'), ('direction','INBOUND'), ('etherscan_api_key','x'*257),
    ('etherscan_api_key','embedded\nsecret')])
def test_invalid_live_inputs_fail_before_network_and_hide_request_key(client, monkeypatch, field, value):
    async def unexpected(*args,**kwargs):
        pytest.fail('Invalid request reached live provider')
    monkeypatch.setattr(EtherscanClient,'trace_real_wallet_on_etherscan',unexpected)
    payload = {'wallet_address': A, 'etherscan_api_key':'must-never-echo-secret', field:value}
    response = client.post('/api/v1/trail/live-etherscan',json=payload)
    assert response.status_code == 422
    assert 'must-never-echo-secret' not in response.text
    assert 'embedded' not in response.text
    assert 'x'*257 not in response.text


def test_live_provider_failure_stays_error_not_synthetic_graph(client,monkeypatch):
    async def failure(*args, **kwargs):
        raise EtherscanAPIError('Explorer gagal diakses; coba lagi.')
    monkeypatch.setattr(EtherscanClient,'trace_real_wallet_on_etherscan',failure)
    response = client.post('/api/v1/trail/live-etherscan',json={'wallet_address':A})
    assert response.status_code == 502
    assert 'nodes' not in response.json()


def test_existing_expand_uses_mainnet_engine_and_deduplicates_seed(client,trace,monkeypatch):
    mock_engine(monkeypatch)
    graph = trace('p2p-dex-cold-wallet')
    source = next(node for node in graph['nodes'] if node.get('address'))
    response = client.get(f"/api/v1/trail/address/{source['address']}/expand", params={
        'trace_id': graph['trace_id'], 'chain':source['chain'],'live':'true'})
    assert response.status_code == 200,response.text
    assert response.json()['simulation'] is False
    assert len(response.json()['nodes']) == 1
    assert response.json()['edges'][0]['source'] == source['id']


def test_non_ethereum_live_expansion_rejected(client, trace):
    graph = trace('p2p-full-cycle')
    source = next(node for node in graph['nodes'] if node.get('address'))
    response = client.get(f"/api/v1/trail/address/{source['address']}/expand",params={
        'trace_id':graph['trace_id'],'chain':source['chain'],'live':'true'})
    assert response.status_code == 422
