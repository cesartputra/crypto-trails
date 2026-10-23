"""Synthetic contract fixtures only; no credentials or external network are used."""
import asyncio
from decimal import Decimal

import httpx
import pytest

from schemas import TrailEdge, TrailNode
from services import cryptoapis_continuation as module
from services.cryptoapis_continuation import CryptoAPIsAPIError, CryptoAPIsContinuation
from services.etherscan_client import CHAIN_TOKENS
from services.chains import BASE58

D = Decimal
EVM = ['0x' + format(i, '040x') for i in range(1, 10)]
BTC = ['bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh', '1BoatSLRHtKNngkdXEeobR76b53LETtpyT', '1BitcoinEaterAddressDontSendf59kuE']


def b58(raw):
    number = int.from_bytes(raw, 'big'); result = ''
    while number:
        number, remainder = divmod(number, 58); result = BASE58[remainder] + result
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + result


SOL = [b58(bytes([i]) * 32) for i in range(1, 9)]
SIG = b58(b'\x07' * 64)


class Consumer:
    def __init__(self):
        self.logs = []
    async def consume_pakd_wallet_reverse_lookup_api(self, address, chain):
        return None


@pytest.fixture(autouse=True)
def safe_config(monkeypatch):
    monkeypatch.setattr(module, 'validate_config', lambda: {'CRYPTOAPIS_API_KEY': 'fixture-key', 'CRYPTOAPIS_BASE_URL': 'https://rest.cryptoapis.io'})


def graph(chain='ETHEREUM', address=None):
    address = address or EVM[3]
    node = TrailNode(id='legacy-frontier', label='Legacy frontier', node_zone='ONCHAIN_WALLET', node_type='LIVE_UNATTRIBUTED_WALLET',
                     address=address, chain=chain, hop=6 if chain == 'BTC' else 3, scanner_provider='LEGACY', balance_crypto=D('12.5'), metadata={'simulation': False})
    return {'nodes': [node], 'edges': [], 'warnings': [], 'metadata': {'provider': 'LEGACY', 'scanner_provider': 'LEGACY', 'requests_made': 7, 'truncated': False}, 'pakd_api_exchange_logs': []}


def response_rows(rows, more=False):
    return {'data': {'items': rows, 'hasMore': more, 'limit': 10}}


def balance(unit='ETH', amount=None):
    amount = amount or ('1.23456789' if unit == 'BTC' else '1.234567891' if unit == 'SOL' else '1.234567890123456789')
    return {'data': {'item': {'confirmedBalance': {'amount': amount, 'unit': unit}}}}


def native(sender, recipient, tx='a', amount='0.123456789012345678', unit='ETH', timestamp=1700000000):
    return {'sender': sender, 'recipient': recipient, 'hash': '0x' + tx * 64, 'timestamp': timestamp, 'status': '0x1', 'value': {'amount': amount, 'unit': unit}}


def token(sender, recipient, contract=None, symbol='USDC', decimals=6, amount='123.456789', tx='b'):
    return {'sender': sender, 'recipient': recipient, 'transactionHash': '0x' + tx * 64, 'timestamp': 1700000000,
            'tokenData': {'contractAddress': contract or CHAIN_TOKENS['ETHEREUM']['USDC'][0], 'symbol': symbol, 'standard': 'ERC-20', 'fungibleValues': {'amount': amount, 'decimals': decimals}}}


def extend(result, handler, **kwargs):
    calls = []
    def record(request):
        calls.append(request)
        return handler(request)
    engine = CryptoAPIsContinuation(transport=httpx.MockTransport(record), pakd_consumer=Consumer())
    output = asyncio.run(engine.extend(result, source_chain=kwargs.pop('source_chain', 'ETHEREUM'), **kwargs))
    return output, calls


def test_continues_exactly_hops_four_five_six_preserving_legacy_ids_and_balances():
    original = graph()
    def handle(req):
        address, category = req.url.path.split('/')[-2:]
        if category == 'balance': return httpx.Response(200, json=balance())
        rows = [] if category == 'tokens-transfers' else [native(address, EVM[EVM.index(address) + 1], str(EVM.index(address)))]
        return httpx.Response(200, json=response_rows(rows))
    result, calls = extend(original, handle)
    assert result is original
    assert [(n.address, n.hop) for n in result['nodes']] == [(EVM[3], 3), (EVM[4], 4), (EVM[5], 5), (EVM[6], 6)]
    assert result['nodes'][0].id == 'legacy-frontier' and result['nodes'][0].balance_crypto == D('12.5')
    assert result['nodes'][0].scanner_provider == 'LEGACY'
    assert {req.url.path.split('/')[-2] for req in calls} == set(EVM[3:6])
    assert len(result['edges']) == 3 and all(e.crypto_amount == D('0.123456789012345678') for e in result['edges'])
    assert result['metadata']['requests_made'] == 7 + len(calls)
    assert result['metadata']['cryptoapis_requests_made'] == len(calls)
    assert result['metadata']['max_hops'] == 6 and 'CRYPTOAPIS' in result['metadata']['provider']
    for item in [*result['nodes'][1:], *result['edges']]:
        assert item.scanner_provider == 'CRYPTOAPIS' and item.metadata['evidence_source'] == 'CRYPTOAPIS_LIVE'
        assert item.amount_idr == 0 and item.metadata['ownership'] == 'UNKNOWN'
    assert all(n.taint_score_pct is None for n in result['nodes'])
    for request in calls:
        assert request.method == 'GET' and request.url.host == 'rest.cryptoapis.io'
        assert request.headers['X-API-Key'] == 'fixture-key' and 'fixture-key' not in str(request.url)
        assert request.headers['Accept'] == request.headers['Content-Type'] == 'application/json'
        assert not request.content and not request.url.path.startswith('/v2')


@pytest.mark.parametrize('chain,slug,unit,address', [('ETHEREUM','ethereum','ETH',EVM[3]),('BSC','binance-smart-chain','BNB',EVM[3]),('POLYGON','polygon','POL',EVM[3]),('ARBITRUM','arbitrum','ETH',EVM[3]),('OPTIMISM','optimism','ETH',EVM[3]),('BASE','base','ETH',EVM[3]),('BTC','utxo/bitcoin','BTC',BTC[0]),('SOLANA','solana','SOL',SOL[3])])
def test_all_eight_chains_use_documented_paths_and_history(chain, slug, unit, address):
    def handle(req):
        return httpx.Response(200, json=balance(unit) if req.url.path.endswith('/balance') else response_rows([]))
    result, calls = extend(graph(chain, address), handle, source_chain=chain, max_hops=4)
    prefix = '/addresses-latest/' + (slug if chain in {'BTC', 'SOLANA'} else 'evm/' + slug) + '/mainnet/' + address + '/'
    assert calls and all(str(req.url.path).startswith(prefix) for req in calls)
    assert result['metadata']['cryptoapis_history_window_days'] == (1 if chain == 'SOLANA' else 14)
    assert all('sync' not in req.url.path and 'subscription' not in req.url.path for req in calls)
    if chain == 'SOLANA': assert all('sortingOrder' not in req.url.params for req in calls)


def test_canonical_tokens_use_display_decimals_reject_spoof_symbols_and_wrong_decimals():
    def handle(req):
        if req.url.path.endswith('/balance'): return httpx.Response(200, json=balance())
        rows = [token(EVM[3], EVM[4]), token(EVM[3], EVM[5], contract=EVM[8]), token(EVM[3], EVM[6], decimals=18, tx='c')]
        return httpx.Response(200, json=response_rows(rows))
    result, calls = extend(graph(), handle, max_hops=4, token_filter='USDC')
    assert len(result['edges']) == 1 and result['edges'][0].asset == 'USDC'
    assert result['edges'][0].crypto_amount == D('123.456789')
    assert not any(req.url.path.endswith('/transactions') for req in calls)
    assert any('tidak valid' in w or 'kanonis' in w for w in result['warnings'])


@pytest.mark.parametrize('status,expected', [(401,'autentikasi'),(402,'kredit'),(403,'akses'),(429,'kuota'),(302,'redirect'),(500,'HTTP 500')])
def test_http_errors_are_sanitized_and_never_fallback(status, expected):
    def handle(req): return httpx.Response(status, text='fixture-key secret-body https://evil.example', headers={'Location': 'https://evil.example'})
    with pytest.raises(CryptoAPIsAPIError) as failure:
        extend(graph(), handle, max_hops=4)
    assert expected.lower() in str(failure.value).lower()
    assert all(secret not in str(failure.value) for secret in ['fixture-key','secret-body','evil.example'])


@pytest.mark.parametrize('payload', [{}, {'data': {'items': []}}, {'data': {'items': {}, 'hasMore': False}}, {'error': {'message': 'fixture-key'}}])
def test_malformed_list_schema_fails_instead_of_empty_success(payload):
    def handle(req): return httpx.Response(200, json=balance() if req.url.path.endswith('/balance') else payload)
    with pytest.raises(CryptoAPIsAPIError): extend(graph(), handle, max_hops=4)


@pytest.mark.parametrize('amount', [True, 'NaN', 'Infinity', '-1', 1.5, '1e9999'])
def test_invalid_native_amounts_are_skipped_with_explicit_warning(amount):
    def handle(req):
        if req.url.path.endswith('/balance'): return httpx.Response(200, json=balance())
        return httpx.Response(200, json=response_rows([native(EVM[3], EVM[4], amount=amount)]))
    result, _ = extend(graph(), handle, max_hops=4, token_filter='ETH')
    assert not result['edges'] and any('tidak valid' in warning for warning in result['warnings'])


def test_request_budget_and_global_node_edge_caps_include_legacy_graph(monkeypatch):
    monkeypatch.setattr(CryptoAPIsContinuation, 'MAX_REQUESTS_EVM', 2)
    def handle(req): return httpx.Response(200, json=balance() if req.url.path.endswith('/balance') else response_rows([native(EVM[3], EVM[4])], True))
    result, calls = extend(graph(), handle, max_hops=6)
    assert len(calls) <= 2 and result['metadata']['truncated']
    original = graph()
    original['nodes'].extend(TrailNode(id='old-' + str(i), label='old', node_zone='ONCHAIN_WALLET', node_type='OLD') for i in range(63))
    result, calls = extend(original, handle, max_hops=4)
    assert len(result['nodes']) == 64 and not result['edges'] and result['metadata']['truncated']


def test_empty_frontier_performs_no_requests():
    original = graph(); original['nodes'][0].hop = 2
    result, calls = extend(original, lambda req: pytest.fail('no frontier must not call API'))
    assert not calls and result['metadata']['provider'] == 'LEGACY'


def test_duplicate_frontiers_and_cycle_do_not_repeat_queries_or_duplicate_evidence():
    original = graph(); original['nodes'].append(TrailNode(id='seed',label='seed',node_zone='ONCHAIN_WALLET',node_type='LIVE_UNATTRIBUTED_WALLET',chain='ETHEREUM',address=EVM[0],hop=0))
    def handle(req):
        if req.url.path.endswith('/balance'): return httpx.Response(200, json=balance())
        return httpx.Response(200, json=response_rows([native(EVM[3], EVM[0])]))
    result, calls = extend(original, handle, token_filter='ETH')
    assert {req.url.path.split('/')[-2] for req in calls} == {EVM[3]}
    assert len(result['edges']) == 1 and result['edges'][0].target == 'seed'


def test_solana_uses_native_movements_not_signer_or_balance_changes():
    def handle(req):
        if req.url.path.endswith('/balance'): return httpx.Response(200, json=balance('SOL'))
        rows = [{'signature': SIG, 'signer': SOL[0], 'timestamp':1700000000,
                 'nativeMovements': [{'senderAddress':SOL[3], 'recipientAddress':SOL[4], 'amount':'0.000000009', 'unit':'SOL'}],
                 'nativeBalanceChanges':[{'address':SOL[5], 'change':'9999'}], 'tokenMovements':[]}]
        return httpx.Response(200, json=response_rows(rows))
    result, _ = extend(graph('SOLANA', SOL[3]), handle, source_chain='SOLANA', max_hops=4)
    assert len(result['edges']) == 1 and result['edges'][0].tx_hash == SIG
    assert result['edges'][0].crypto_amount == D('0.000000009')
    assert {n.address for n in result['nodes']} == {SOL[3], SOL[4]}


def test_bitcoin_keeps_transaction_node_and_txid_without_pairwise_attribution():
    txid, wtxid = 'a'*64, 'b'*64
    def handle(req):
        if req.url.path.endswith('/balance'): return httpx.Response(200, json=balance('BTC'))
        rows = [{'id':txid,'hash':wtxid,'timestamp':1700000000,
                 'inputs':[{'addresses':[BTC[0]],'value':{'amount':'1','unit':'BTC'}},{'addresses':[BTC[2]],'value':{'amount':'2','unit':'BTC'}}],
                 'outputs':[{'addresses':[BTC[1]],'value':{'amount':'2.7','unit':'BTC'}},{'addresses':[BTC[0]],'value':{'amount':'0.299','unit':'BTC'}}]}]
        return httpx.Response(200, json=response_rows(rows))
    result, _ = extend(graph('BTC', BTC[0]), handle, source_chain='BTC', max_hops=4)
    tx = next(n for n in result['nodes'] if n.node_type == 'BITCOIN_TRANSACTION')
    assert tx.metadata['tx_hash'] == txid and tx.hop == 7
    spends = [e for e in result['edges'] if e.edge_type == 'BITCOIN_INPUT_SPEND']
    outputs = [e for e in result['edges'] if e.edge_type == 'BITCOIN_TRANSACTION_OUTPUT']
    assert len(spends) == 2 and len(outputs) == 2
    assert all(e.target == tx.id for e in spends) and all(e.source == tx.id for e in outputs)
    assert all(e.tx_hash == txid and e.metadata['change_or_payment'] == 'UNKNOWN' for e in result['edges'])
    assert all(e.metadata['attribution'] == 'UTXO_PARTICIPATION_ONLY' for e in result['edges'])
    assert all(n.hop <= 8 for n in result['nodes'])


def test_incoming_transfer_places_new_sender_at_next_wallet_hop():
    def handle(req):
        return httpx.Response(200, json=balance() if req.url.path.endswith('/balance') else
            response_rows([native(EVM[4], EVM[3])]))
    result, _ = extend(graph(), handle, max_hops=4, token_filter='ETH', direction='BOTH')
    assert [(node.address, node.hop) for node in result['nodes']] == [(EVM[3], 3), (EVM[4], 4)]
    assert result['nodes'][1].metadata['wallet_hop'] == 4
    assert result['edges'][0].source == result['nodes'][1].id


@pytest.mark.parametrize('output', ['2', '0.000000001'])
def test_invalid_bitcoin_totals_or_fractional_satoshis_create_no_evidence(output):
    def handle(req):
        return httpx.Response(200, json=response_rows([{'id': 'c'*64, 'timestamp': 1700000000,
            'inputs': [{'addresses': [BTC[0]], 'value': {'amount': '1', 'unit': 'BTC'}}],
            'outputs': [{'addresses': [BTC[1]], 'value': {'amount': output, 'unit': 'BTC'}}]}]))
    result, _ = extend(graph('BTC', BTC[0]), handle, source_chain='BTC', max_hops=4)
    assert not result['edges'] and len(result['nodes']) == 1
    assert any('tidak valid' in warning for warning in result['warnings'])


def test_bitcoin_change_reuse_follows_new_outpoint_without_refetching_address():
    original = graph('BTC', BTC[0])
    original['edges'] = [TrailEdge(id='prior-output', source='prior-tx', target='legacy-frontier',
        edge_type='BITCOIN_TRANSACTION_OUTPUT', label='prior', chain='BTC', tx_hash='1'*64,
        metadata={'output_index': 0})]
    def handle(req):
        assert req.url.path.endswith('/transactions')
        if BTC[1] in req.url.path or BTC[2] in req.url.path:
            return httpx.Response(200, json=response_rows([]))
        rows = [{'id': 'b'*64, 'timestamp': 1700000001,
            'inputs': [{'addresses': [BTC[0]], 'transactionId': 'a'*64, 'outputIndex': 1, 'value': {'amount': '0.4', 'unit': 'BTC'}}],
            'outputs': [{'addresses': [BTC[2]], 'value': {'amount': '0.399', 'unit': 'BTC'}}]},
            {'id': 'a'*64, 'timestamp': 1700000000,
            'inputs': [{'addresses': [BTC[0]], 'transactionId': '1'*64, 'outputIndex': 0, 'value': {'amount': '1', 'unit': 'BTC'}}],
            'outputs': [{'addresses': [BTC[1]], 'value': {'amount': '0.599', 'unit': 'BTC'}},
                        {'addresses': [BTC[0]], 'value': {'amount': '0.4', 'unit': 'BTC'}}]}]
        return httpx.Response(200, json=response_rows(rows))
    result, calls = extend(original, handle, source_chain='BTC', max_hops=5)
    assert any(edge.tx_hash == 'b'*64 for edge in result['edges'])
    assert next(node for node in result['nodes'] if node.address == BTC[2]).metadata['wallet_hop'] == 5
    assert len([req for req in calls if BTC[0] in req.url.path]) == 1
