import asyncio
from decimal import Decimal
import json

import httpx
import pytest

from schemas import TrailEdge, TrailNode
from services import multichain_scanner as module
from services.chains import BASE58, explorer_address_url, explorer_tx_url, is_valid_address, is_valid_tx_hash
from services.multichain_scanner import MultichainScanner, ScannerAPIError, SYSTEM_PROGRAM

BTC = 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh'
BTC_OTHER = '1BoatSLRHtKNngkdXEeobR76b53LETtpyT'
BTC_THIRD = '1BitcoinEaterAddressDontSendf59kuE'
SOL = 'So11111111111111111111111111111111111111112'
SOL_OTHER = '11111111111111111111111111111111'


def b58(raw):
    number = int.from_bytes(raw, 'big')
    result = ''
    while number:
        number, remainder = divmod(number, 58)
        result = BASE58[remainder] + result
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + result


def signature(byte):
    return b58(bytes([byte]) * 64)


class Consumer:
    def __init__(self):
        self.logs = []
        self.lookups = []

    async def consume_pakd_wallet_reverse_lookup_api(self, address, chain):
        self.lookups.append((address, chain))
        return None


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    async def immediate():
        pass
    monkeypatch.setattr(module, '_throttle', immediate)


def btc_tx(txid='a' * 64, sender=BTC, recipient=BTC_OTHER, confirmed=True):
    return {'txid': txid, 'vin': [{'txid': '9' * 64, 'vout': 0,
        'prevout': {'scriptpubkey_address': sender, 'value': 100_000_000}}],
        'vout': [{'scriptpubkey_address': recipient, 'value': 70_000_000},
                 {'scriptpubkey_address': sender, 'value': 29_990_000}], 'fee': 10_000,
        'status': {'confirmed': confirmed, 'block_time': 1_700_000_000} if confirmed else {'confirmed': False}}


def btc_handler(rows, requests, fallback=False):
    def handle(request):
        requests.append(request)
        if fallback and request.url.host == 'mempool.space':
            return httpx.Response(503, text='provider unavailable')
        if request.url.path.endswith('/txs'):
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json={'chain_stats': {'funded_txo_sum': 80_000_000, 'spent_txo_sum': 30_000_000},
                                        'mempool_stats': {'funded_txo_sum': 100_000, 'spent_txo_sum': 0}})
    return handle


def run_btc(rows, **kwargs):
    requests = []
    scanner = MultichainScanner(transport=httpx.MockTransport(btc_handler(rows, requests)), pakd_consumer=Consumer())
    return asyncio.run(scanner.trace_wallet(BTC, 'BTC', max_hops=1, **kwargs)), requests


def test_bitcoin_uses_transaction_node_without_allocating_other_outputs_or_change():
    graph, requests = run_btc([btc_tx()], etherscan_api_key='must-not-be-sent')
    assert len(graph['nodes']) == 3 and len(graph['edges']) == 3
    tx = next(node for node in graph['nodes'] if node.node_type == 'BITCOIN_TRANSACTION')
    seed = next(node for node in graph['nodes'] if node.address == BTC)
    assert tx.address is None and tx.explorer_url == 'https://mempool.space/tx/' + 'a' * 64
    assert seed.balance_crypto == Decimal('0.50100000')
    spend = next(edge for edge in graph['edges'] if edge.edge_type == 'BITCOIN_INPUT_SPEND')
    outputs = [edge for edge in graph['edges'] if edge.edge_type == 'BITCOIN_TRANSACTION_OUTPUT']
    assert spend.crypto_amount == 1 and all(edge.source == tx.id for edge in outputs)
    assert sum(edge.crypto_amount for edge in outputs) == Decimal('0.99990000')
    assert all(edge.metadata['change_or_payment'] == 'UNKNOWN' for edge in graph['edges'])
    assert all(edge.amount_idr == 0 and edge.timestamp.year == 2023 for edge in graph['edges'])
    assert all(node.taint_score_pct is None for node in graph['nodes'])
    assert all(node.chain_family == 'BITCOIN' and node.scanner_provider == 'MEMPOOL_SPACE' for node in graph['nodes'])
    assert all(edge.chain_branch == 'BTC' for edge in graph['edges'])
    assert all('must-not-be-sent' not in str(request.url) + request.content.decode() for request in requests)
    assert graph['metadata']['simulation'] is False


def test_bitcoin_fallback_is_real_same_endpoint_and_provider_is_disclosed():
    requests = []
    scanner = MultichainScanner(transport=httpx.MockTransport(btc_handler([btc_tx()], requests, fallback=True)), pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet(BTC, 'BITCOIN', max_hops=1))
    assert [request.url.host for request in requests] == ['mempool.space', 'blockstream.info'] * 2
    assert requests[0].url.path == requests[1].url.path == '/api/address/' + BTC + '/txs'
    assert graph['metadata']['scanner_provider'] == 'BLOCKSTREAM_ESPLORA'
    assert all(node.scanner_provider == 'BLOCKSTREAM_ESPLORA' for node in graph['nodes'])
    assert graph['edges'] and all(edge.metadata['evidence_source'] == 'BLOCKSTREAM_ESPLORA_LIVE' for edge in graph['edges'])


@pytest.mark.parametrize('response', [httpx.Response(200, text='bad json'), httpx.Response(200, json={'unexpected': []})])
def test_bitcoin_malformed_provider_response_does_not_silently_fallback(response):
    calls = []
    def handle(request):
        calls.append(request)
        return response
    scanner = MultichainScanner(transport=httpx.MockTransport(handle), pakd_consumer=Consumer())
    with pytest.raises(ScannerAPIError):
        asyncio.run(scanner.trace_wallet(BTC, 'BTC', max_hops=1))
    assert len(calls) == 1 and calls[0].url.host == 'mempool.space'


def test_bitcoin_malformed_and_non_conserving_rows_are_skipped_without_fake_transactions():
    overspend = btc_tx('b' * 64)
    overspend['vout'][0]['value'] = 200_000_000
    bad_input = btc_tx('c' * 64)
    bad_input['vin'] = [None]
    graph, _ = run_btc([overspend, bad_input, {'txid': 'invalid'}, btc_tx()])
    assert len(graph['edges']) == 3
    assert [node.metadata['tx_hash'] for node in graph['nodes'] if node.node_type == 'BITCOIN_TRANSACTION'] == ['a' * 64]
    assert any('tidak valid' in warning for warning in graph['warnings'])


def test_bitcoin_unconfirmed_has_no_invented_timestamp():
    graph, _ = run_btc([btc_tx(confirmed=False)])
    assert all(edge.timestamp is None and edge.metadata['confirmed'] is False for edge in graph['edges'])
    assert any('belum dikonfirmasi' in warning for warning in graph['warnings'])


def test_bitcoin_next_hop_requires_actual_spending_of_discovered_outpoint():
    first = btc_tx()
    unrelated = btc_tx('b' * 64, BTC_OTHER, BTC_THIRD)
    related = btc_tx('c' * 64, BTC_OTHER, BTC_THIRD)
    related['vin'][0].update(txid=first['txid'], vout=0)
    related['vin'][0]['prevout']['value'] = 70_000_000
    related['vout'][0]['value'] = 40_000_000
    related['status']['block_time'] += 60
    calls = []
    def handle(request):
        calls.append(request)
        rows = [first] if request.url.path == '/api/address/' + BTC + '/txs' else [unrelated, related]
        if request.url.path.endswith('/txs'):
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json={'chain_stats': {'funded_txo_sum': 0, 'spent_txo_sum': 0}})
    scanner = MultichainScanner(transport=httpx.MockTransport(handle), pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet(BTC, 'BTC', max_hops=2))
    hashes = {edge.tx_hash for edge in graph['edges']}
    assert first['txid'] in hashes and related['txid'] in hashes and unrelated['txid'] not in hashes
    assert graph['metadata']['requests_made'] <= 24


def sol_tx(sig, instructions, err=None, inner=None):
    return {'blockTime': 1_700_000_000, 'meta': {'err': err, 'innerInstructions': inner or []},
            'transaction': {'signatures': [sig], 'message': {'instructions': instructions}}}


def transfer(source=SOL, destination=SOL_OTHER, lamports=125_000_001):
    return {'program': 'system', 'programId': SYSTEM_PROGRAM,
            'parsed': {'type': 'transfer', 'info': {'source': source, 'destination': destination, 'lamports': lamports}}}


def sol_handler(records, transactions, calls):
    def handle(request):
        assert request.url == module.SOLANA_RPC
        body = json.loads(request.content)
        calls.append(body)
        result = {'context': {'slot': 1}, 'value': 1_234_567_890} if body['method'] == 'getBalance' else records if body['method'] == 'getSignaturesForAddress' else transactions[body['params'][0]]
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})
    return handle


def run_sol(records, transactions, **kwargs):
    calls = []
    scanner = MultichainScanner(transport=httpx.MockTransport(sol_handler(records, transactions, calls)), pakd_consumer=Consumer())
    return asyncio.run(scanner.trace_wallet(SOL, 'SOLANA', max_hops=1, **kwargs)), calls


def test_solana_parses_actual_native_transfers_balance_and_skips_failed_transactions():
    good, failed, meta_failed = signature(1), signature(2), signature(3)
    records = [{'signature': sig, 'err': err} for sig, err in [(good, None), (failed, {'InstructionError': [0, 'bad']}), (meta_failed, None)]]
    graph, calls = run_sol(records, {good: sol_tx(good, [transfer()]), meta_failed: sol_tx(meta_failed, [transfer()], err={'bad': 'instruction'})}, etherscan_api_key='do-not-send')
    assert len(graph['edges']) == 1 and graph['edges'][0].crypto_amount == Decimal('0.125000001')
    edge = graph['edges'][0]
    assert edge.tx_hash == good and edge.chain == 'SOLANA' and edge.scanner_provider == 'SOLANA_MAINNET_RPC'
    assert edge.explorer_url == 'https://solscan.io/tx/' + good
    assert edge.timestamp.year == 2023 and edge.amount_idr == 0
    assert next(node for node in graph['nodes'] if node.address == SOL).balance_crypto == Decimal('1.234567890')
    assert [item['status'] for item in graph['metadata']['activity']] == ['PARSED_NATIVE_TRANSFER', 'FAILED', 'FAILED']
    fetched = [call['params'][0] for call in calls if call['method'] == 'getTransaction']
    assert fetched == [good, meta_failed]
    assert all('do-not-send' not in json.dumps(call) for call in calls)
    assert calls[1]['params'][1]['limit'] == 10
    assert calls[2]['params'][1] == {'encoding': 'jsonParsed', 'commitment': 'confirmed', 'maxSupportedTransactionVersion': 0}


def test_solana_signer_and_unsupported_program_do_not_become_outgoing_money():
    sig = signature(4)
    spoof = transfer()
    spoof['programId'] = SOL
    transaction = sol_tx(sig, [spoof, {'programId': SYSTEM_PROGRAM, 'parsed': {'type': 'createAccount'}}])
    transaction['transaction']['message']['accountKeys'] = [{'pubkey': SOL, 'signer': True}, {'pubkey': SOL_OTHER, 'signer': False}]
    graph, _ = run_sol([{'signature': sig, 'err': None}], {sig: transaction})
    assert len(graph['nodes']) == 1 and not graph['edges']
    assert graph['metadata']['activity'][0]['status'] == 'UNSUPPORTED_INSTRUCTIONS'
    assert graph['metadata']['activity'][0]['explorer_url'] == 'https://solscan.io/tx/' + sig


def test_solana_parses_inner_system_transfer_and_inbound_only_with_both():
    sig = signature(5)
    tx = sol_tx(sig, [], inner=[{'index': 0, 'instructions': [transfer(SOL_OTHER, SOL)]}])
    records = [{'signature': sig, 'err': None}]
    outbound, _ = run_sol(records, {sig: tx})
    both, _ = run_sol(records, {sig: tx}, direction='BOTH')
    assert not outbound['edges']
    assert len(both['edges']) == 1 and both['edges'][0].metadata['instruction_index'] == 'inner:0:0'


def test_solana_rejects_malformed_signature_and_mismatched_rpc_transaction():
    requested, other = signature(6), signature(7)
    graph, calls = run_sol([{'signature': 'not-a-signature', 'err': None}, {'signature': requested, 'err': None}],
                          {requested: sol_tx(other, [transfer()])})
    assert not graph['edges'] and len(graph['metadata']['activity']) == 1
    assert graph['metadata']['activity'][0]['status'] == 'NOT_AVAILABLE'
    assert len([call for call in calls if call['method'] == 'getTransaction']) == 1


def test_solana_rpc_failure_is_sanitized_and_never_returns_demo_graph():
    def handle(request):
        return httpx.Response(200, json={'jsonrpc': '2.0', 'error': {'message': 'private-provider-secret'}})
    scanner = MultichainScanner(transport=httpx.MockTransport(handle), pakd_consumer=Consumer())
    with pytest.raises(ScannerAPIError) as error:
        asyncio.run(scanner.trace_wallet(SOL, 'SOLANA', max_hops=1))
    assert 'private-provider-secret' not in str(error.value)


def test_solana_unsupported_version_remains_activity_and_other_signatures_continue():
    unsupported, good = signature(9), signature(10)
    records = [{'signature': sig, 'err': None} for sig in [unsupported, good]]
    calls = []
    normal_handler = sol_handler(records, {good: sol_tx(good, [transfer()])}, calls)
    def handle(request):
        body = json.loads(request.content)
        if body['method'] == 'getTransaction' and body['params'][0] == unsupported:
            return httpx.Response(200, json={'jsonrpc': '2.0', 'error': {'code': -32015, 'message': 'not exposed'}})
        return normal_handler(request)
    scanner = MultichainScanner(transport=httpx.MockTransport(handle), pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet(SOL, 'SOLANA', max_hops=1))
    assert len(graph['edges']) == 1 and graph['edges'][0].tx_hash == good
    assert [item['status'] for item in graph['metadata']['activity']] == ['UNSUPPORTED_TRANSACTION_VERSION', 'PARSED_NATIVE_TRANSFER']
    assert all('not exposed' not in warning for warning in graph['warnings'])


@pytest.mark.parametrize('chain,address,token', [('BTC', BTC[:-1] + 'q', 'ALL'), ('SOLANA', '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp', 'ALL'), ('BTC', BTC, 'USDT'), ('SOLANA', SOL, 'BTC')])
def test_wrong_address_or_cross_asset_rejected_before_network(chain, address, token):
    scanner = MultichainScanner(transport=httpx.MockTransport(lambda request: pytest.fail('invalid input reached network')), pakd_consumer=Consumer())
    with pytest.raises(ValueError):
        asyncio.run(scanner.trace_wallet(address, chain, token_filter=token))


def test_chain_address_and_explorer_validation_are_family_specific():
    assert is_valid_address('BITCOIN', BTC) and is_valid_address('BTC', BTC.upper())
    assert is_valid_address('BTC', BTC_OTHER) and not is_valid_address('BTC', BTC_OTHER[:-1] + 'U')
    assert not is_valid_address('BTC', BTC[:5].upper() + BTC[5:])
    assert is_valid_address('SOLANA', SOL) and not is_valid_address('SOLANA', BTC)
    assert is_valid_tx_hash('SOLANA', signature(8)) and not is_valid_tx_hash('SOLANA', 'a' * 64)
    assert is_valid_tx_hash('BTC', 'a' * 64) and not is_valid_tx_hash('BTC', '0x' + 'a' * 64)
    assert explorer_address_url('SOL', SOL) == 'https://solscan.io/account/' + SOL
    assert explorer_tx_url('BTC', 'a' * 64) == 'https://mempool.space/tx/' + 'a' * 64
    assert explorer_tx_url('SOLANA', 'https://evil.example') is None


@pytest.mark.parametrize('address,valid', [
    ('BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4', True),
    ('BC1SW50QGDZ25J', True),
    ('bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs', True),
    ('bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0', True),
    ('bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd', False),
    ('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh', False),
    ('BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R', False),
    ('bc1pw5dgrnzv', False),
    ('bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf', False),
    ('bc1gmk9yu', False),
])
def test_bitcoin_mainnet_checksum_matches_official_bip350_vectors(address, valid):
    assert is_valid_address('BTC', address) is valid


def test_evm_delegates_to_existing_scanner_and_stamps_actual_provider(monkeypatch):
    address = '0x' + 'a' * 40
    captured = {}
    async def trace(self, seed, **kwargs):
        captured.update(seed=seed, kwargs=kwargs)
        return {'metadata': {'provider': 'BLOCKSCOUT'}, 'nodes': [TrailNode(id='a', label='a', node_zone='ONCHAIN_WALLET', node_type='LIVE_UNATTRIBUTED_WALLET', address=address, chain='BASE')],
                'edges': [TrailEdge(id='e', source='a', target='b', label='ETH', edge_type='NATIVE_TRANSFER', chain='BASE')], 'warnings': []}
    monkeypatch.setattr(module.EtherscanClient, 'trace_real_wallet_on_etherscan', trace)
    scanner = MultichainScanner(pakd_consumer=Consumer())
    graph = asyncio.run(scanner.trace_wallet(address, 'BASE', max_hops=2, token_filter='ETH', auto_scan_cross_chain=True))
    assert captured['kwargs']['source_chain'] == 'BASE' and captured['kwargs']['auto_scan_cross_chain'] is True
    assert captured['seed'] == address
    assert all(item.scanner_provider == 'BLOCKSCOUT' and item.chain_family == 'EVM' and item.chain_branch == 'BASE' for item in graph['nodes'] + graph['edges'])
