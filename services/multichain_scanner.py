"""Read-only dedicated scanners. Live failures never turn into scenario transactions."""
import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import httpx

from schemas import TrailEdge, TrailNode
from services.chains import CHAIN_CONFIG, normalize_chain, is_valid_address, is_valid_tx_hash, explorer_address_url, explorer_tx_url
from services.etherscan_client import EtherscanClient, _config, _throttle
from services.pakd_api_consumer import PAKDAPIConsumer

D = Decimal
BTC_PROVIDERS = (("MEMPOOL_SPACE", "https://mempool.space/api"), ("BLOCKSTREAM_ESPLORA", "https://blockstream.info/api"))
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
SOLSCAN_BASE_URL = "https://pro-api.solscan.io/v2.0"
SOLSCAN_NATIVE_TOKEN = "So11111111111111111111111111111111111111111"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


class ScannerAPIError(RuntimeError):
    """Sanitized public scanner failure; no raw provider body is exposed."""


class _ProviderUnavailable(ScannerAPIError):
    pass


class _UnsupportedSolanaVersion(ScannerAPIError):
    pass


class _SolscanPlanRequired(ScannerAPIError):
    pass


def _uint(value, maximum=2**64 - 1):
    if isinstance(value, D):
        if not value.is_finite() or not 0 <= value <= maximum or value != value.to_integral_value():
            raise ValueError('Nilai scanner wajib integer dalam batas yang didukung')
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isdigit():
        raise ValueError('Nilai scanner wajib integer non-negatif')
    result = int(value)
    if result > maximum:
        raise ValueError('Nominal scanner melewati batas')
    return result


def _time(value):
    if value is None:
        return None
    stamp = datetime.fromtimestamp(_uint(value), timezone.utc)
    if stamp.year < 2009 or stamp > datetime.now(timezone.utc):
        raise ValueError('Timestamp scanner tidak valid')
    return stamp


class MultichainScanner:
    PAGE_SIZE = 10
    MAX_REQUESTS = 24
    MAX_NODES = 64
    MAX_EDGES = 128

    def __init__(self, transport=None, pakd_consumer=None):
        self.transport = transport
        self.pakd_consumer = pakd_consumer or PAKDAPIConsumer()

    async def trace_wallet(self, wallet_address, source_chain, max_hops=2, token_filter='ALL', direction='OUTBOUND',
                           etherscan_api_key=None, auto_scan_cross_chain=False):
        chain = normalize_chain(source_chain)
        if not is_valid_address(chain, wallet_address):
            raise ValueError('Alamat tidak valid untuk chain yang dipilih')
        if chain == 'BTC' and wallet_address.lower().startswith('bc1'):
            wallet_address = wallet_address.lower()
        if type(max_hops) is not int or not 1 <= max_hops <= 3:
            raise ValueError('max_hops harus 1 sampai 3')
        if direction not in {'OUTBOUND', 'BOTH'}:
            raise ValueError('Arah scanner tidak didukung')
        if CHAIN_CONFIG[chain]['chain_family'] == 'EVM':
            result = await EtherscanClient(api_key=etherscan_api_key, transport=self.transport,
                pakd_consumer=self.pakd_consumer).trace_real_wallet_on_etherscan(wallet_address, max_hops=max_hops,
                    token_filter=token_filter, direction=direction, source_chain=chain, auto_scan_cross_chain=auto_scan_cross_chain)
            result['metadata'].update(scanner_provider=result['metadata']['provider'], chain_family='EVM')
            for item in [*result['nodes'], *result['edges']]:
                item.scanner_provider = result['metadata']['provider']
                item.chain_family = 'EVM'
                item.chain_branch = normalize_chain(item.chain or chain)
                item.metadata.update(scanner_provider=item.scanner_provider, chain_family='EVM', chain_branch=item.chain_branch)
            return result
        if chain not in {'BTC', 'SOLANA'}:
            raise ValueError('Scanner live tidak tersedia untuk chain ini')
        native = CHAIN_CONFIG[chain]['native_symbol']
        if token_filter not in {'ALL', 'ETH', native}:
            raise ValueError('Scanner ini hanya mendukung aset native chain yang dipilih')
        solscan_key, solscan_url = '', SOLSCAN_BASE_URL
        if chain == 'SOLANA':
            config = _config()
            solscan_key = config.get('SOLSCAN_API_KEY', '').strip()
            if solscan_key:
                solscan_url = (config.get('SOLSCAN_BASE_URL') or SOLSCAN_BASE_URL).strip().rstrip('/')
                if len(solscan_key) > 2048 or any(ord(char) < 33 or ord(char) > 126 for char in solscan_key):
                    raise ScannerAPIError('Format SOLSCAN_API_KEY tidak valid; gunakan key dari Solscan API Management')
                if solscan_url != SOLSCAN_BASE_URL:
                    raise ScannerAPIError('SOLSCAN_BASE_URL harus https://pro-api.solscan.io/v2.0 tanpa query atau kredensial')
        state = {'requests': 0, 'truncated': False, 'providers': set(), 'activity': [], 'seed': wallet_address}
        warnings = ['LIVE READ ONLY: aktivitas publik bukan bukti kepemilikan, taint atau nilai recovery.',
            'Identitas pada respons Scoped Mirror API PAKD merupakan seed sintetis demo.']
        if auto_scan_cross_chain:
            warnings.append('Auto-pivot EVM tidak diterapkan pada BTC/Solana; tidak ada korelasi lintas-family otomatis.')
        try:
            async with asyncio.timeout(60):
                async with httpx.AsyncClient(transport=self.transport, timeout=10, follow_redirects=False) as client:
                    if chain == 'BTC':
                        nodes, edges = await self._bitcoin(client, wallet_address, max_hops, direction, state, warnings)
                    elif solscan_key:
                        try:
                            nodes, edges = await self._solscan(client, wallet_address, max_hops, direction, state, warnings, solscan_key, solscan_url)
                        except _SolscanPlanRequired as exc:
                            warnings.append(str(exc) + '; pelacakan dilanjutkan melalui Solana Mainnet RPC publik dengan sumber data terpisah.')
                            state.update(fallback_from='SOLSCAN_PRO_API', fallback_reason='SOLSCAN_PLAN_REQUIRED')
                            state['providers'].clear()
                            state['activity'].clear()
                            try:
                                nodes, edges = await self._solana(client, wallet_address, max_hops, direction, state, warnings)
                            except ScannerAPIError as rpc_error:
                                raise ScannerAPIError(str(exc) + '; fallback RPC juga gagal: ' + str(rpc_error)) from None
                    else:
                        nodes, edges = await self._solana(client, wallet_address, max_hops, direction, state, warnings)
        except TimeoutError:
            raise ScannerAPIError('Batas waktu scanner tercapai; coba satu hop atau ulangi nanti') from None
        if state['truncated']:
            warnings.append('HASIL TERBATAS: batas halaman, request, node atau edge tercapai; bukan riwayat lengkap.')
        if not edges:
            warnings.append('Tidak ada transfer native yang dapat didekode sesuai filter pada halaman yang tersedia.')
        provider = ', '.join(sorted(state['providers'])) or CHAIN_CONFIG[chain]['scanner_provider']
        return {'nodes': list(nodes.values()), 'edges': edges, 'warnings': warnings,
            'pakd_api_exchange_logs': self.pakd_consumer.logs,
            'metadata': {'simulation': False, 'network': chain + '_MAINNET', 'source_chain': chain,
                'chain_id': None, 'chain_family': CHAIN_CONFIG[chain]['chain_family'], 'provider': provider,
                'scanner_provider': provider, 'seed_wallet_address': wallet_address, 'max_hops': max_hops,
                'token_filter': token_filter, 'direction': direction, 'auto_scan_cross_chain': False,
                'page_size': self.PAGE_SIZE, 'requests_made': state['requests'], 'truncated': state['truncated'],
                'fallback_from': state.get('fallback_from'), 'fallback_reason': state.get('fallback_reason'),
                'activity': state['activity'], 'fetched_at': datetime.now(timezone.utc).isoformat()}}

    async def _json(self, client, state, method, url, payload=None, *, params=None, headers=None, provider=None):
        if state['requests'] >= self.MAX_REQUESTS:
            state['truncated'] = True
            raise ScannerAPIError('Budget request scanner tercapai; coba satu hop')
        state['requests'] += 1
        await _throttle()
        try:
            async with client.stream(method, url, json=payload, params=params, headers=headers) as response:
                if provider == 'Solscan Pro API':
                    messages = {401: 'autentikasi gagal (HTTP 401); periksa SOLSCAN_API_KEY dan aktivasi key',
                        403: 'akses ditolak (HTTP 403); periksa paket API, aktivasi key, atau pembatasan IP',
                        429: 'batas permintaan/kuota tercapai (HTTP 429); tunggu lalu coba kembali'}
                    if response.status_code in messages:
                        if response.status_code == 401:
                            denied = bytearray()
                            async for chunk in response.aiter_bytes():
                                denied.extend(chunk[:max(4096 - len(denied), 0)])
                                if len(denied) >= 4096:
                                    break
                            try:
                                payload = json.loads(denied)
                            except ValueError:
                                payload = None
                            error = payload.get('errors') if isinstance(payload, dict) else None
                            if isinstance(error, dict) and str(error.get('code')) == '401' and error.get('message') == 'Unauthorized: Please upgrade your api key level.':
                                raise _SolscanPlanRequired('Solscan Pro API menolak akses endpoint akun: level/paket API key perlu ditingkatkan (HTTP 401)')
                        raise ScannerAPIError(provider + ': ' + messages[response.status_code])
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ScannerAPIError('Respons scanner melebihi batas aman')
            return json.loads(body, parse_float=D)
        except httpx.HTTPError:
            raise _ProviderUnavailable((provider or 'Scanner publik') + ' gagal diakses; periksa koneksi atau ketersediaan provider. Tidak ada data simulasi pengganti') from None
        except (ValueError, TypeError):
            raise ScannerAPIError((provider or 'Scanner publik') + ' mengembalikan respons tidak valid; tidak ada data simulasi pengganti') from None

    async def _wallet(self, nodes, chain, address, hop, provider, state):
        key = chain + ':' + address
        if key in nodes:
            return nodes[key]
        if len(nodes) >= self.MAX_NODES:
            state['truncated'] = True
            return None
        match = await self.pakd_consumer.consume_pakd_wallet_reverse_lookup_api(address, chain)
        metadata = {'simulation': False, 'is_live': True, 'evidence_source': provider + '_LIVE',
            'scanner_provider': provider, 'chain_family': CHAIN_CONFIG[chain]['chain_family'], 'chain_branch': chain,
            'ownership': 'UNKNOWN', 'taint_status': 'UNKNOWN', 'balance_status': 'NOT_FETCHED', 'is_seed_wallet': address == state['seed']}
        if match:
            metadata.update(mirror_match=match, identity_source='SYNTHETIC_PAKD_SCOPED_MIRROR_REST_API')
        nodes[key] = TrailNode(id=chain.lower() + '-' + sha256(address.encode()).hexdigest()[:20],
            label=(f"{match['pakd_name']} · {match['user_uid']} (API demo)" if match else address[:10] + '…' + address[-4:]),
            node_zone='ONCHAIN_WALLET', node_type='PAKD_MIRROR_MATCH' if match else 'LIVE_UNATTRIBUTED_WALLET',
            entity_label='Akun publik; kepemilikan belum diverifikasi', address=address, chain=chain,
            asset=CHAIN_CONFIG[chain]['native_symbol'], hop=hop, taint_score_pct=None, status='LIVE_READ_ONLY',
            chain_family=CHAIN_CONFIG[chain]['chain_family'], scanner_provider=provider, chain_branch=chain,
            explorer_url=explorer_address_url(chain, address), metadata=metadata)
        return nodes[key]

    async def _btc_get(self, client, state, path):
        for provider, base in BTC_PROVIDERS:
            try:
                value = await self._json(client, state, 'GET', base + path)
                if not isinstance(value, (dict, list)):
                    raise ScannerAPIError('Struktur Esplora tidak valid')
                state['providers'].add(provider)
                return value, provider
            except _ProviderUnavailable:
                if state['truncated']:
                    raise
        raise ScannerAPIError('Mempool.space dan Blockstream tidak tersedia; tidak ada data demo pengganti')

    async def _bitcoin(self, client, seed, max_hops, direction, state, warnings):
        nodes, edges, visited, seen = {}, [], set(), set()
        queue = deque([(seed, 0, None, None)])
        warnings.extend(['Bitcoin memakai UTXO: setiap output dapat berupa change atau pembayaran; asal input tidak membuktikan pemilik output.',
            'Maksimal 10 transaksi terbaru per alamat. Node transaksi memisahkan kontribusi input dari seluruh output; nominal tidak dianggap dana laporan.'])
        await self._wallet(nodes, 'BTC', seed, 0, 'MEMPOOL_SPACE', state)
        while queue:
            address, depth, outpoint, after = queue.popleft()
            visit = (address, outpoint)
            if visit in visited or depth >= max_hops:
                continue
            if state['requests'] >= self.MAX_REQUESTS - 1:
                state['truncated'] = True
                break
            visited.add(visit)
            rows, provider = await self._btc_get(client, state, '/address/' + address + '/txs')
            if not isinstance(rows, list):
                raise ScannerAPIError('Daftar transaksi Bitcoin tidak valid')
            state['truncated'] |= len(rows) >= self.PAGE_SIZE
            wallet = await self._wallet(nodes, 'BTC', address, depth * 2, provider, state)
            if not wallet:
                break
            wallet.metadata.update(scanner_provider=provider, evidence_source=provider + '_LIVE')
            wallet.scanner_provider = provider
            for raw in rows[:self.PAGE_SIZE]:
                try:
                    if not isinstance(raw, dict) or not is_valid_tx_hash('BTC', raw.get('txid')):
                        raise ValueError()
                    txid, inputs, outputs = raw['txid'], raw['vin'], raw['vout']
                    if not isinstance(inputs, list) or not inputs or not isinstance(outputs, list) or not outputs or len(inputs) > 1000 or len(outputs) > 1000:
                        raise ValueError()
                    if any(not isinstance(item, dict) for item in inputs + outputs):
                        raise ValueError()
                    values = [_uint(out['value'], 2_100_000_000_000_000) for out in outputs]
                    known_inputs = [item.get('prevout') for item in inputs]
                    if outpoint is not None and not any((item.get('txid'), item.get('vout')) == outpoint for item in inputs):
                        continue
                    own_input = sum(_uint(item['value']) for item in known_inputs if isinstance(item, dict) and item.get('scriptpubkey_address') == address)
                    own_output = any(out.get('scriptpubkey_address') == address for out in outputs)
                    if not own_input and (direction != 'BOTH' or not own_output):
                        continue
                    if all(isinstance(item, dict) for item in known_inputs):
                        input_total = sum(_uint(item['value']) for item in known_inputs)
                        if sum(values) > input_total or 'fee' in raw and input_total - sum(values) != _uint(raw['fee']):
                            raise ValueError()
                    status = raw.get('status', {})
                    if not isinstance(status, dict) or not isinstance(status.get('confirmed'), bool):
                        raise ValueError()
                    timestamp = _time(status.get('block_time')) if status['confirmed'] else None
                    if after is not None and timestamp is not None and timestamp < after:
                        continue
                    if txid in seen:
                        continue
                    if len(nodes) >= self.MAX_NODES or len(edges) + 1 >= self.MAX_EDGES:
                        state['truncated'] = True
                        break
                    seen.add(txid)
                    tx_node = TrailNode(id='btc-tx-' + txid, label='Transaksi BTC · ' + txid[:8],
                        node_zone='ONCHAIN_WALLET', node_type='BITCOIN_TRANSACTION', chain='BTC', asset='BTC', hop=depth * 2 + 1,
                        status='CONFIRMED' if status['confirmed'] else 'UNCONFIRMED', explorer_url=explorer_tx_url('BTC', txid),
                        chain_family='BITCOIN', scanner_provider=provider, chain_branch='BTC',
                        metadata={'simulation': False, 'is_live': True, 'evidence_source': provider + '_LIVE', 'scanner_provider': provider,
                            'chain_family': 'BITCOIN', 'chain_branch': 'BTC', 'tx_hash': txid, 'input_count': len(inputs),
                            'output_count': len(outputs), 'confirmed': status['confirmed'], 'ownership': 'UNKNOWN', 'taint_status': 'UNKNOWN'})
                    nodes['BTC:TX:' + txid] = tx_node
                    evidence = {'simulation': False, 'is_live': True, 'evidence_source': provider + '_LIVE', 'scanner_provider': provider,
                        'chain_family': 'BITCOIN', 'chain_branch': 'BTC', 'attribution': 'UTXO_PARTICIPATION_ONLY',
                        'change_or_payment': 'UNKNOWN', 'confirmed': status['confirmed'], 'taint_status': 'UNKNOWN'}
                    if own_input:
                        edges.append(TrailEdge(id='btc-input-' + txid + '-' + wallet.id, source=wallet.id, target=tx_node.id,
                            edge_type='BITCOIN_INPUT_SPEND', label=f'{D(own_input).scaleb(-8)} BTC input', crypto_amount=D(own_input).scaleb(-8),
                            asset='BTC', chain='BTC', tx_hash=txid, timestamp=timestamp, explorer_url=explorer_tx_url('BTC', txid),
                            chain_family='BITCOIN', scanner_provider=provider, chain_branch='BTC', metadata=evidence))
                    for index, (out, value) in enumerate(zip(outputs, values)):
                        if len(edges) >= self.MAX_EDGES:
                            state['truncated'] = True
                            break
                        recipient = out.get('scriptpubkey_address')
                        if value == 0 or not is_valid_address('BTC', recipient):
                            continue
                        target = await self._wallet(nodes, 'BTC', recipient, depth * 2 + 2, provider, state)
                        if not target:
                            continue
                        edges.append(TrailEdge(id=f'btc-output-{txid}-{index}', source=tx_node.id, target=target.id,
                            edge_type='BITCOIN_TRANSACTION_OUTPUT', label=f'{D(value).scaleb(-8)} BTC output · atribusi belum diketahui',
                            crypto_amount=D(value).scaleb(-8), asset='BTC', chain='BTC', tx_hash=txid, timestamp=timestamp,
                            explorer_url=explorer_tx_url('BTC', txid), chain_family='BITCOIN', scanner_provider=provider, chain_branch='BTC',
                            metadata={**evidence, 'output_index': index}))
                        queue.append((recipient, depth + 1, (txid, index), timestamp))
                except (ValueError, TypeError, KeyError, OverflowError, OSError):
                    warnings.append('Record Bitcoin tidak valid/tidak didukung dilewati.')
        try:
            if state['requests'] < self.MAX_REQUESTS:
                stats, provider = await self._btc_get(client, state, '/address/' + seed)
                confirmed, pending = stats['chain_stats'], stats.get('mempool_stats', {})
                balance = _uint(confirmed['funded_txo_sum']) - _uint(confirmed['spent_txo_sum'])
                balance += _uint(pending.get('funded_txo_sum', 0)) - _uint(pending.get('spent_txo_sum', 0))
                if balance < 0:
                    raise ValueError()
                nodes['BTC:' + seed].balance_crypto = D(balance).scaleb(-8)
                nodes['BTC:' + seed].metadata.update(balance_status='FETCHED', balance_source=provider, balance_includes_mempool=True)
        except (ScannerAPIError, ValueError, TypeError, KeyError):
            warnings.append('Saldo BTC belum tersedia; graph hanya memuat bukti transaksi yang berhasil diambil.')
        if any(node.status == 'UNCONFIRMED' for node in nodes.values()):
            warnings.append('Sebagian transaksi BTC belum dikonfirmasi dan dapat berubah; timestamp blok belum tersedia.')
        return nodes, edges

    async def _solscan_get(self, client, state, endpoint, params, api_key, base_url):
        payload = await self._json(client, state, 'GET', base_url + endpoint, params=params,
            headers={'token': api_key, 'accept': 'application/json'}, provider='Solscan Pro API')
        if not isinstance(payload, dict) or payload.get('success') is not True or 'data' not in payload:
            raise ScannerAPIError('Solscan Pro API menolak permintaan atau mengembalikan respons tidak valid; periksa key dan paket API')
        state['providers'].add('SOLSCAN_PRO_API')
        return payload['data']

    async def _solscan(self, client, seed, max_hops, direction, state, warnings, api_key, base_url):
        provider = 'SOLSCAN_PRO_API'
        nodes, edges, visited, seen, activity_signatures = {}, [], set(), set(), set()
        queue = deque([(seed, 0)])
        warnings.append('Solscan Pro API: maksimal 10 transfer native SOL terbaru/alamat; SPL-token, swap, dan atribusi dana laporan belum didukung.')
        wallet = await self._wallet(nodes, 'SOLANA', seed, 0, provider, state)
        balance = await self._solscan_get(client, state, '/account/detail', {'address': seed}, api_key, base_url)
        try:
            if not isinstance(balance, dict) or balance.get('account') != seed:
                raise ValueError()
            wallet.balance_crypto = D(_uint(balance['lamports'])).scaleb(-9)
        except (ValueError, TypeError, KeyError):
            raise ScannerAPIError('Saldo Solscan Pro API tidak valid atau tidak terikat pada alamat yang diminta') from None
        wallet.metadata['balance_status'] = 'FETCHED'
        while queue:
            address, depth = queue.popleft()
            if address in visited or depth >= max_hops:
                continue
            if state['requests'] >= self.MAX_REQUESTS:
                state['truncated'] = True
                break
            visited.add(address)
            params = {'address': address, 'token': SOLSCAN_NATIVE_TOKEN, 'activity_type[]': 'ACTIVITY_SPL_TRANSFER',
                'page': 1, 'page_size': self.PAGE_SIZE, 'sort_by': 'block_time', 'sort_order': 'desc', 'exclude_amount_zero': 'true'}
            if direction == 'OUTBOUND':
                params['flow'] = 'out'
            rows = await self._solscan_get(client, state, '/account/transfer', params, api_key, base_url)
            if not isinstance(rows, list):
                raise ScannerAPIError('Daftar transfer Solscan Pro API tidak valid')
            state['truncated'] |= len(rows) >= self.PAGE_SIZE
            occurrences = {}
            for index, row in enumerate(rows[:self.PAGE_SIZE]):
                try:
                    if not isinstance(row, dict) or row.get('activity_type') != 'ACTIVITY_SPL_TRANSFER' or row.get('token_address') != SOLSCAN_NATIVE_TOKEN or _uint(row.get('token_decimals')) != 9:
                        raise ValueError()
                    signature, source, target = row['trans_id'], row['from_address'], row['to_address']
                    if not is_valid_tx_hash('SOLANA', signature) or not is_valid_address('SOLANA', source) or not is_valid_address('SOLANA', target):
                        raise ValueError()
                    if source != address and (direction != 'BOTH' or target != address):
                        continue
                    lamports = _uint(row['amount'])
                    timestamp = _time(row['block_time'])
                    if not lamports or timestamp is None:
                        continue
                    identity = (signature, source, target, lamports, row['block_time'])
                    occurrences[identity] = occurrences.get(identity, 0) + 1
                    key = (*identity, occurrences[identity])
                    if key in seen:
                        continue
                    if len(edges) >= self.MAX_EDGES:
                        state['truncated'] = True
                        break
                    sender = await self._wallet(nodes, 'SOLANA', source, depth, provider, state)
                    recipient = await self._wallet(nodes, 'SOLANA', target, depth + 1, provider, state)
                    if not sender or not recipient:
                        continue
                    seen.add(key)
                    edges.append(TrailEdge(id='solscan-' + sha256(str(key).encode()).hexdigest()[:24],
                        source=sender.id, target=recipient.id, edge_type='SOLANA_NATIVE_TRANSFER',
                        label=f'{D(lamports).scaleb(-9)} SOL', crypto_amount=D(lamports).scaleb(-9), asset='SOL', chain='SOLANA',
                        tx_hash=signature, timestamp=timestamp, explorer_url=explorer_tx_url('SOLANA', signature),
                        chain_family='SOLANA', scanner_provider=provider, chain_branch='SOLANA',
                        metadata={'simulation': False, 'is_live': True, 'evidence_source': provider + '_LIVE',
                            'scanner_provider': provider, 'chain_family': 'SOLANA', 'chain_branch': 'SOLANA',
                            'provider_row_index': index, 'taint_status': 'UNKNOWN'}))
                    if signature not in activity_signatures:
                        activity_signatures.add(signature)
                        state['activity'].append({'signature': signature, 'explorer_url': explorer_tx_url('SOLANA', signature),
                            'timestamp': timestamp.isoformat(), 'status': 'PARSED_NATIVE_TRANSFER'})
                    queue.append((target if source == address else source, depth + 1))
                except (ValueError, TypeError, KeyError, OverflowError, OSError):
                    warnings.append('Transfer Solscan Pro API tidak valid atau bukan transfer native SOL dilewati.')
        return nodes, edges

    async def _rpc(self, client, state, method, params):
        payload = await self._json(client, state, 'POST', SOLANA_RPC,
            {'jsonrpc': '2.0', 'id': state['requests'] + 1, 'method': method, 'params': params}, provider='Solana Mainnet RPC')
        if method == 'getTransaction' and isinstance(payload, dict) and isinstance(payload.get('error'), dict) and payload['error'].get('code') == -32015:
            raise _UnsupportedSolanaVersion('Versi transaksi belum didukung decoder native SOL')
        if not isinstance(payload, dict) or payload.get('error') is not None or 'result' not in payload:
            raise ScannerAPIError('Solana Mainnet RPC menolak permintaan; periksa ketersediaan dan kuota')
        state['providers'].add('SOLANA_MAINNET_RPC')
        return payload['result']

    async def _solana(self, client, seed, max_hops, direction, state, warnings):
        nodes, edges, visited, seen = {}, [], set(), set()
        queue = deque([(seed, 0)])
        warnings.extend(['Solana RPC: maksimal 10 signature terbaru/alamat; signature mereferensikan accountKeys, bukan otomatis transfer dari pemilik wallet.',
            'Graph hanya menampilkan instruksi native SOL SystemProgram transfer yang berhasil. SPL-token, swap, staking dan instruksi lain belum didekode.'])
        wallet = await self._wallet(nodes, 'SOLANA', seed, 0, 'SOLANA_MAINNET_RPC', state)
        balance = await self._rpc(client, state, 'getBalance', [seed, {'commitment': 'confirmed'}])
        if not isinstance(balance, dict):
            raise ScannerAPIError('Respons saldo Solana tidak valid')
        try:
            wallet.balance_crypto = D(_uint(balance['value'])).scaleb(-9)
        except (ValueError, KeyError, TypeError):
            raise ScannerAPIError('Nominal saldo Solana tidak valid') from None
        wallet.metadata['balance_status'] = 'FETCHED'
        while queue:
            address, depth = queue.popleft()
            if address in visited or depth >= max_hops:
                continue
            if state['requests'] >= self.MAX_REQUESTS:
                state['truncated'] = True
                break
            visited.add(address)
            signatures = await self._rpc(client, state, 'getSignaturesForAddress', [address, {'limit': self.PAGE_SIZE, 'commitment': 'confirmed'}])
            if not isinstance(signatures, list):
                raise ScannerAPIError('Daftar signature Solana tidak valid')
            state['truncated'] |= len(signatures) >= self.PAGE_SIZE
            for record in signatures[:self.PAGE_SIZE]:
                if not isinstance(record, dict) or 'err' not in record or not is_valid_tx_hash('SOLANA', record.get('signature')):
                    warnings.append('Signature Solana tidak valid dilewati.')
                    continue
                signature = record['signature']
                if signature in seen:
                    continue
                seen.add(signature)
                activity = {'signature': signature, 'explorer_url': explorer_tx_url('SOLANA', signature), 'status': 'FAILED' if record.get('err') is not None else 'NOT_AVAILABLE'}
                state['activity'].append(activity)
                if record.get('err') is not None:
                    continue
                if state['requests'] >= self.MAX_REQUESTS:
                    state['truncated'] = True
                    break
                try:
                    transaction = await self._rpc(client, state, 'getTransaction', [signature, {'encoding': 'jsonParsed', 'commitment': 'confirmed', 'maxSupportedTransactionVersion': 0}])
                except _UnsupportedSolanaVersion:
                    activity['status'] = 'UNSUPPORTED_TRANSACTION_VERSION'
                    warnings.append('Versi transaksi Solana belum didukung; signature tersedia sebagai aktivitas tanpa edge transfer.')
                    continue
                if transaction is None:
                    continue
                try:
                    if signature not in transaction['transaction']['signatures']:
                        raise ValueError()
                    meta = transaction['meta']
                    if not isinstance(meta, dict) or 'err' not in meta:
                        raise ValueError()
                    if meta['err'] is not None:
                        activity['status'] = 'FAILED'
                        continue
                    timestamp = _time(transaction.get('blockTime'))
                    activity['timestamp'] = timestamp.isoformat() if timestamp else None
                    instructions = [('outer:' + str(index), instruction) for index, instruction in enumerate(transaction['transaction']['message']['instructions'])]
                    for inner in meta.get('innerInstructions', []) or []:
                        instructions.extend((f"inner:{inner['index']}:{index}", instruction) for index, instruction in enumerate(inner['instructions']))
                    activity['status'] = 'UNSUPPORTED_INSTRUCTIONS'
                    for index, instruction in instructions:
                        if not isinstance(instruction, dict) or instruction.get('programId') != SYSTEM_PROGRAM:
                            continue
                        parsed = instruction.get('parsed')
                        if not isinstance(parsed, dict) or parsed.get('type') not in {'transfer', 'transferWithSeed'}:
                            continue
                        info = parsed.get('info', {})
                        source, target = info.get('source'), info.get('destination')
                        if not is_valid_address('SOLANA', source) or not is_valid_address('SOLANA', target):
                            continue
                        lamports = _uint(info['lamports'])
                        if not lamports or source != address and (direction != 'BOTH' or target != address):
                            continue
                        if len(edges) >= self.MAX_EDGES:
                            state['truncated'] = True
                            break
                        sender = await self._wallet(nodes, 'SOLANA', source, depth, 'SOLANA_MAINNET_RPC', state)
                        recipient = await self._wallet(nodes, 'SOLANA', target, depth + 1, 'SOLANA_MAINNET_RPC', state)
                        if not sender or not recipient:
                            continue
                        activity['status'] = 'PARSED_NATIVE_TRANSFER'
                        edges.append(TrailEdge(id='sol-' + sha256((signature + ':' + index).encode()).hexdigest()[:24],
                            source=sender.id, target=recipient.id, edge_type='SOLANA_NATIVE_TRANSFER',
                            label=f'{D(lamports).scaleb(-9)} SOL', crypto_amount=D(lamports).scaleb(-9), asset='SOL', chain='SOLANA',
                            tx_hash=signature, timestamp=timestamp, explorer_url=explorer_tx_url('SOLANA', signature),
                            chain_family='SOLANA', scanner_provider='SOLANA_MAINNET_RPC', chain_branch='SOLANA',
                            metadata={'simulation': False, 'is_live': True, 'evidence_source': 'SOLANA_MAINNET_RPC_LIVE',
                                'scanner_provider': 'SOLANA_MAINNET_RPC', 'chain_family': 'SOLANA', 'chain_branch': 'SOLANA',
                                'instruction_index': index, 'taint_status': 'UNKNOWN'}))
                        queue.append((target if source == address else source, depth + 1))
                except (ValueError, TypeError, KeyError, OverflowError, OSError):
                    warnings.append('Transaksi/instruksi Solana tidak valid atau tidak didukung dilewati.')
        return nodes, edges
