"""Bounded Crypto APIs continuation of the primary scanner's third-hop frontier."""
import asyncio
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import re
from math import ceil, isfinite

import httpx

from schemas import TrailEdge, TrailNode
from services.chains import CHAIN_CONFIG, normalize_chain, is_valid_address, is_valid_tx_hash, explorer_address_url, explorer_tx_url
from services.cryptoapis_config import validate_config
from services.cryptoapis_limits import (PAGE_SIZE, MAX_REQUESTS, MAX_CREDITS_PER_SCAN, MAX_RETRIES,
    estimate_request_credits, request_slot, defer_requests)
from services.etherscan_client import CHAIN_TOKENS, KNOWN_BRIDGE_CONTRACTS, KNOWN_EVM_ENTITIES
from services.pakd_api_consumer import PAKDAPIConsumer

SLUGS = {'ETHEREUM': 'ethereum', 'BSC': 'binance-smart-chain', 'POLYGON': 'polygon',
         'ARBITRUM': 'arbitrum', 'OPTIMISM': 'optimism', 'BASE': 'base'}
D = Decimal


class CryptoAPIsAPIError(RuntimeError):
    """Safe failure without upstream response bodies or credentials."""


class _ContinuationStopped(Exception):
    def __init__(self, reason, message, http_status=None, retry_after=None):
        super().__init__(message)
        self.reason, self.http_status, self.retry_after = reason, http_status, retry_after


def _amount(value, zero=False):
    if not isinstance(value, str) or len(value) > 100 or not re.fullmatch(r'\d+(?:\.\d+)?', value):
        raise ValueError('Nominal tidak valid')
    amount = D(value)
    if amount.adjusted() > 77 or amount < 0 or not zero and amount == 0:
        raise ValueError('Nominal tidak valid')
    return amount


def _time(value):
    if type(value) is not int or value < 0:
        raise ValueError('Timestamp tidak valid')
    return datetime.fromtimestamp(value, timezone.utc)


def _coin_amount(chain, value, zero=False):
    amount = _amount(value, zero=zero)
    if -amount.as_tuple().exponent > (8 if chain == 'BTC' else 9 if chain == 'SOLANA' else 18):
        raise ValueError('Nominal native tidak valid')
    return amount


def _address(chain, address):
    if not is_valid_address(chain, address):
        raise ValueError('Alamat tidak valid')
    return address.lower() if chain in SLUGS or chain == 'BTC' and address.lower().startswith('bc1') else address


def _hash(chain, value):
    if chain in SLUGS and isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{64}', value):
        value = '0x' + value
    if not is_valid_tx_hash(chain, value):
        raise ValueError('Hash tidak valid')
    return value.lower() if chain != 'SOLANA' else value


def _credit_header(headers, name):
    raw = headers.get(name, '')
    try:
        value = D(raw) if len(raw) <= 32 else D('NaN')
        return value if value.is_finite() and 0 <= value <= 10**12 else None
    except (ArithmeticError, ValueError, TypeError):
        return None


def _retry_after(raw):
    if not raw or len(raw) > 128:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            date = parsedate_to_datetime(raw)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if isfinite(seconds) else None


class CryptoAPIsContinuation:
    MAX_REQUESTS_EVM = MAX_REQUESTS
    MAX_REQUESTS_NATIVE = MAX_REQUESTS
    MAX_CREDITS_PER_SCAN = MAX_CREDITS_PER_SCAN
    MAX_NODES = 64
    MAX_EDGES = 128
    PAGE_SIZE = PAGE_SIZE

    def __init__(self, transport=None, pakd_consumer=None):
        self.settings = validate_config()
        self.transport = transport
        self.pakd_consumer = pakd_consumer or PAKDAPIConsumer()

    async def _get(self, client, path, *, chain, solana=False):
        if path in self.cache:
            return self.cache[path]
        estimate = estimate_request_credits(chain, path.rsplit('/', 1)[-1])
        params = {'limit': self.PAGE_SIZE}
        if not solana:
            params['sortingOrder'] = 'descending'
        try:
            for attempt in range(MAX_RETRIES + 1):
                if self.requests_base + self.requests >= self.request_limit or self.credits_base + self.credits_consumed + self.credits_estimated + estimate > self.MAX_CREDITS_PER_SCAN:
                    raise _ContinuationStopped('BUDGET_LIMIT', 'Anggaran mode Free Crypto APIs tercapai; hasil yang tersedia dipertahankan.')
                if self.available_for_requests is not None and self.available_for_requests < estimate:
                    raise _ContinuationStopped('CREDIT_LIMIT', 'Sisa kredit Crypto APIs yang teramati tidak cukup untuk request berikutnya.')
                async with request_slot():
                    self.requests += 1
                    self.credits_estimated += estimate
                    async with client.stream('GET', self.settings['CRYPTOAPIS_BASE_URL'] + path, params=params) as response:
                        consumed = _credit_header(response.headers, 'x-credits-consumed')
                        available = _credit_header(response.headers, 'x-credits-available')
                        if consumed is not None:
                            self.credits_estimated -= estimate
                            self.credits_consumed += ceil(consumed)
                        if available is not None:
                            self.credits_available = int(available)
                            self.available_for_requests = self.credits_available
                        elif self.available_for_requests is not None:
                            self.available_for_requests = max(0, self.available_for_requests - (ceil(consumed) if consumed is not None else estimate))
                        status = response.status_code
                        if status == 429:
                            delay = _retry_after(response.headers.get('Retry-After'))
                            delay = max(1.2, delay if delay is not None else 2.0)
                            defer_requests(delay)
                            retry_fits = self.requests_base + self.requests < self.request_limit and self.credits_base + self.credits_consumed + self.credits_estimated + estimate <= self.MAX_CREDITS_PER_SCAN
                            available_fits = self.available_for_requests is None or self.available_for_requests >= estimate
                            if attempt < MAX_RETRIES and delay <= 10 and retry_fits and available_fits:
                                continue
                            raise _ContinuationStopped('RATE_LIMIT', 'Crypto APIs membatasi throughput kredit (HTTP 429); kelanjutan dihentikan sementara.', 429, delay)
                        if status == 402:
                            raise _ContinuationStopped('CREDIT_LIMIT', 'Kredit Crypto APIs tidak cukup (HTTP 402); hasil fase awal tetap tersedia.', 402)
                        if status == 403:
                            raise _ContinuationStopped('ACCESS_LIMIT', 'Akses Crypto APIs ditolak (HTTP 403); periksa izin key, paket dan mainnet.', 403)
                        if status == 401:
                            raise CryptoAPIsAPIError('Crypto APIs: autentikasi gagal; periksa CRYPTOAPIS_API_KEY di .env (HTTP 401)')
                        if 300 <= status < 400:
                            raise CryptoAPIsAPIError('Crypto APIs: redirect ditolak')
                        if status != 200:
                            raise CryptoAPIsAPIError(f'Crypto APIs gagal (HTTP {status})')
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2_000_000:
                                raise CryptoAPIsAPIError('Respons Crypto APIs melebihi batas aman')
                payload = json.loads(raw)
                if not isinstance(payload, dict) or 'error' in payload or not isinstance(payload.get('data'), dict):
                    raise ValueError()
                data = payload['data']
                if not isinstance(data.get('items'), list) or type(data.get('hasMore')) is not bool:
                    raise ValueError()
                self.success_reads += 1
                self.truncated |= data['hasMore'] or len(data['items']) > self.PAGE_SIZE
                self.cache[path] = data['items'][:self.PAGE_SIZE]
                return self.cache[path]
        except httpx.HTTPError:
            raise CryptoAPIsAPIError('Crypto APIs gagal diakses; periksa koneksi atau ketersediaan layanan') from None
        except (ValueError, TypeError):
            raise CryptoAPIsAPIError('Respons Crypto APIs tidak valid; tidak ada data simulasi pengganti') from None

    def _evidence(self, chain, **extra):
        return {'simulation': False, 'is_live': True, 'evidence_source': 'CRYPTOAPIS_LIVE',
            'scanner_provider': 'CRYPTOAPIS', 'chain_family': CHAIN_CONFIG[chain]['chain_family'],
            'chain_branch': chain, 'ownership': 'UNKNOWN', 'taint_status': 'UNKNOWN', **extra}

    async def _wallet(self, chain, address, depth):
        address = _address(chain, address)
        key = (chain, address)
        if key in self.wallets:
            return self.wallets[key]
        if len(self.result['nodes']) >= self.MAX_NODES:
            self.truncated = True
            return None
        metadata = self._evidence(chain, balance_status='NOT_FETCHED', wallet_hop=depth)
        match = await self.pakd_consumer.consume_pakd_wallet_reverse_lookup_api(address, chain)
        if match:
            metadata.update(mirror_match=match, identity_source='SYNTHETIC_PAKD_SCOPED_MIRROR_REST_API')
        entity = KNOWN_EVM_ENTITIES.get(address) if chain == 'ETHEREUM' else None
        bridge = KNOWN_BRIDGE_CONTRACTS.get(chain, {}).get(address)
        if entity:
            metadata.update(public_entity=entity)
        node = TrailNode(id='cryptoapis-' + chain.lower() + '-' + sha256(address.encode()).hexdigest()[:24],
            label=bridge['name'] if bridge else entity['label'] if entity else address[:10] + '…',
            address=address, chain=chain, node_zone='ONCHAIN_WALLET', node_type='LIVE_UNATTRIBUTED_WALLET',
            hop=depth * 2 if chain == 'BTC' else depth, asset=CHAIN_CONFIG[chain]['native_symbol'],
            explorer_url=explorer_address_url(chain, address), chain_family=CHAIN_CONFIG[chain]['chain_family'],
            scanner_provider='CRYPTOAPIS', chain_branch=chain, is_bridge_node=bool(bridge),
            bridge_protocol_name=bridge['name'] if bridge else None, metadata=metadata)
        self.wallets[key] = node
        self.result['nodes'].append(node)
        return node

    def _fingerprint(self, chain, txid, sender, recipient, amount, asset):
        return chain, txid, sender, recipient, amount, asset

    async def _transfer(self, chain, sender, recipient, amount, asset, txid, timestamp, depth, occurrence, query_address=None, **extra):
        fingerprint = self._fingerprint(chain, txid, sender, recipient, amount, asset)
        if occurrence <= self.seen[fingerprint]:
            return False
        if len(self.result['edges']) >= self.MAX_EDGES:
            self.truncated = True
            return False
        incoming = recipient == query_address and sender != query_address
        source = await self._wallet(chain, sender, depth + 1 if incoming else depth)
        target = await self._wallet(chain, recipient, depth if incoming else depth + 1)
        if not source or not target:
            return False
        self.seen[fingerprint] = occurrence
        identity = repr((*fingerprint, occurrence))
        self.result['edges'].append(TrailEdge(id='cryptoapis-' + sha256(identity.encode()).hexdigest()[:24],
            source=source.id, target=target.id, edge_type='ONCHAIN_TRANSFER', label=f'{amount} {asset}',
            crypto_amount=amount, asset=asset, tx_hash=txid, timestamp=timestamp, chain=chain,
            chain_id=CHAIN_CONFIG[chain]['chain_id'], explorer_url=explorer_tx_url(chain, txid),
            chain_family=CHAIN_CONFIG[chain]['chain_family'], scanner_provider='CRYPTOAPIS', chain_branch=chain,
            metadata=self._evidence(chain, **extra)))
        return True

    def _evm_rows(self, rows, chain, token=False):
        for row in rows:
            try:
                sender, recipient = _address(chain, row['sender']), _address(chain, row['recipient'])
                timestamp = _time(row['timestamp'])
                if token:
                    info = row['tokenData']
                    values = info['fungibleValues']
                    canonical = [(symbol, decimals) for symbol, (contract, decimals) in CHAIN_TOKENS[chain].items()
                        if contract == str(info['contractAddress']).lower()]
                    if info['standard'] != 'ERC-20' or not canonical or type(values['decimals']) is not int or values['decimals'] != canonical[0][1]:
                        raise ValueError()
                    asset, decimals = canonical[0]
                    if self.token_filter != 'ALL' and asset != self.token_filter:
                        continue
                    amount = _amount(values['amount'])
                    if -amount.as_tuple().exponent > decimals:
                        raise ValueError()
                    txid = _hash(chain, row['transactionHash'])
                else:
                    if row['status'] != '0x1':
                        continue
                    asset = CHAIN_CONFIG[chain]['native_symbol']
                    if row['value']['unit'] not in ({'POL', 'MATIC'} if chain == 'POLYGON' else {asset}):
                        raise ValueError()
                    amount = _coin_amount(chain, row['value']['amount'])
                    txid = _hash(chain, row['hash'])
                yield sender, recipient, amount, asset, txid, timestamp
            except (ValueError, KeyError, TypeError, OverflowError, OSError):
                self.result['warnings'].append('Record Crypto APIs EVM tidak valid atau bukan token kanonis dilewati.')

    async def _account(self, client, chain, address, depth):
        family = 'solana' if chain == 'SOLANA' else 'evm/' + SLUGS[chain]
        path = f'/addresses-latest/{family}/mainnet/{address}'
        categories = ['transactions'] if chain == 'SOLANA' or self.token_filter == 'ETH' else ['tokens-transfers'] if self.token_filter in {'USDT', 'USDC'} else ['transactions', 'tokens-transfers']
        for category in categories:
            rows = await self._get(client, path + '/' + category, chain=chain, solana=chain == 'SOLANA')
            if chain == 'SOLANA':
                transfers = []
                for row in rows:
                    try:
                        txid, timestamp = _hash(chain, row['signature']), _time(row['timestamp'])
                        movements = row['nativeMovements']
                        if not isinstance(movements, list):
                            raise ValueError()
                        self.activity.append({'signature': txid, 'timestamp': timestamp.isoformat(), 'scanner_provider': 'CRYPTOAPIS',
                            'execution_status': 'NOT_PROVIDED_BY_API', 'explorer_url': explorer_tx_url(chain, txid)})
                        for movement in movements:
                            if movement['unit'] != 'SOL':
                                raise ValueError()
                            transfers.append((_address(chain, movement['senderAddress']), _address(chain, movement['recipientAddress']),
                                _coin_amount(chain, movement['amount']), 'SOL', txid, timestamp))
                    except (ValueError, TypeError, KeyError, OverflowError, OSError):
                        self.result['warnings'].append('Record Crypto APIs Solana tidak valid/tidak didukung dilewati.')
            else:
                transfers = self._evm_rows(rows, chain, category == 'tokens-transfers')
            occurrences = Counter()
            for sender, recipient, amount, asset, txid, timestamp in transfers:
                if sender != address and (self.direction != 'BOTH' or recipient != address):
                    continue
                fp = self._fingerprint(chain, txid, sender, recipient, amount, asset)
                occurrences[fp] += 1
                if await self._transfer(chain, sender, recipient, amount, asset, txid, timestamp, depth, occurrences[fp], query_address=address):
                    self.queue.append((chain, recipient if sender == address else sender, depth + 1))

    async def _bitcoin(self, client, address, depth):
        path = f'/addresses-latest/utxo/bitcoin/mainnet/{address}'
        rows = await self._get(client, path + '/transactions', chain='BTC')
        for row in rows:
            try:
                txid, timestamp = _hash('BTC', row['id']), _time(row['timestamp'])
                if txid in self.btc_seen:
                    continue
                inputs, outputs = row['inputs'], row['outputs']
                if not isinstance(inputs, list) or not inputs or not isinstance(outputs, list) or not outputs or len(inputs) > 1000 or len(outputs) > 1000:
                    raise ValueError()
                # A script with multiple addresses is not a distinct payment to each address.
                parsed = []
                for side in (inputs, outputs):
                    records = []
                    for index, item in enumerate(side):
                        addresses = item.get('addresses', [])
                        if not isinstance(addresses, list) or len(addresses) != 1:
                            self.result['warnings'].append('Script Bitcoin tanpa satu alamat jelas dilewati; atribusi output belum diketahui.')
                            continue
                        if item['value']['unit'] != 'BTC':
                            raise ValueError()
                        records.append((_address('BTC', addresses[0]), _coin_amount('BTC', item['value']['amount']), index, item))
                    parsed.append(records)
                ins, outs = parsed
                if len(ins) == len(inputs) and len(outs) == len(outputs):
                    with localcontext() as context:
                        context.prec = 100
                        if sum(item[1] for item in outs) > sum(item[1] for item in ins):
                            raise ValueError('Output melebihi input')
                if not any(a == address for a, *_ in ins) and (self.direction != 'BOTH' or not any(a == address for a, *_ in outs)):
                    continue
                outpoints = self.outpoints.get(address, set())
                if outpoints and not any((i.get('transactionId'), i.get('outputIndex')) in outpoints for *_, i in ins):
                    continue
                if len(self.result['nodes']) >= self.MAX_NODES or len(self.result['edges']) >= self.MAX_EDGES:
                    self.truncated = True
                    break
                self.btc_seen.add(txid)
                evidence = self._evidence('BTC', tx_hash=txid, attribution='UTXO_PARTICIPATION_ONLY', change_or_payment='UNKNOWN')
                tx = TrailNode(id='btc-tx-' + txid, label='Transaksi BTC · ' + txid[:8], node_zone='ONCHAIN_WALLET',
                    node_type='BITCOIN_TRANSACTION', chain='BTC', asset='BTC', hop=depth * 2 + 1,
                    explorer_url=explorer_tx_url('BTC', txid), chain_family='BITCOIN', scanner_provider='CRYPTOAPIS',
                    chain_branch='BTC', metadata=evidence)
                self.result['nodes'].append(tx)
                for outgoing, records in ((False, ins), (True, outs)):
                    for account, amount, index, item in records:
                        if len(self.result['edges']) >= self.MAX_EDGES:
                            self.truncated = True
                            break
                        wallet = await self._wallet('BTC', account, depth + 1 if outgoing else depth)
                        if not wallet:
                            continue
                        self.result['edges'].append(TrailEdge(id=f'cryptoapis-btc-{txid}-{outgoing}-{index}',
                            source=tx.id if outgoing else wallet.id, target=wallet.id if outgoing else tx.id,
                            edge_type='BITCOIN_TRANSACTION_OUTPUT' if outgoing else 'BITCOIN_INPUT_SPEND', label=f'{amount} BTC',
                            crypto_amount=amount, asset='BTC', chain='BTC', tx_hash=txid, timestamp=timestamp,
                            explorer_url=explorer_tx_url('BTC', txid), chain_family='BITCOIN', scanner_provider='CRYPTOAPIS',
                            chain_branch='BTC', metadata={**evidence, 'output_index' if outgoing else 'input_index': index}))
                        if outgoing:
                            self.outpoints.setdefault(account, set()).add((txid, index))
                            self.queue.append(('BTC', account, depth + 1))
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                self.result['warnings'].append('Record Crypto APIs Bitcoin tidak valid/tidak didukung dilewati.')

    async def extend(self, result, source_chain, max_hops=6, token_filter='ALL', direction='OUTBOUND', auto_scan_cross_chain=False, start_hop=3, frontier_ids=None):
        chain = normalize_chain(source_chain)
        if chain not in {*SLUGS, 'BTC', 'SOLANA'} or type(max_hops) is not int or not 4 <= max_hops <= 6 or type(start_hop) is not int or not 3 <= start_hop < max_hops:
            raise ValueError('Kelanjutan Crypto APIs hanya untuk hop 4 sampai 6')
        if direction not in {'OUTBOUND', 'BOTH'} or token_filter not in ({'ALL', 'ETH', CHAIN_CONFIG[chain]['native_symbol']} if chain in {'BTC', 'SOLANA'} else {'ALL', 'ETH', 'USDT', 'USDC'}):
            raise ValueError('Filter/arah tidak didukung')
        self.result, self.token_filter, self.direction = result, token_filter, direction
        self.requests, self.truncated = 0, False
        self.requests_base = result['metadata'].get('cryptoapis_requests_made', 0)
        self.credits_base = result['metadata'].get('cryptoapis_credits_consumed', 0) + result['metadata'].get('cryptoapis_credits_estimated', 0)
        self.credits_consumed, self.credits_estimated, self.success_reads = 0, 0, 0
        self.credits_available = result['metadata'].get('cryptoapis_credits_available')
        self.available_for_requests = self.credits_available
        stopped = None
        self.request_limit = self.MAX_REQUESTS_EVM if chain in SLUGS else self.MAX_REQUESTS_NATIVE
        self.wallets, self.queue, self.seen, self.outpoints = {}, deque(), Counter(), {}
        self.activity, self.cache = [], {}
        visited = set()
        self.btc_seen = {n.metadata.get('tx_hash') for n in result['nodes'] if n.node_type == 'BITCOIN_TRANSACTION'}
        by_id = {n.id: n for n in result['nodes']}
        for node in result['nodes']:
            if node.address and node.chain:
                canonical = normalize_chain(node.chain)
                key = canonical, _address(canonical, node.address)
                self.wallets[key] = node
                depth = node.metadata.get('wallet_hop', node.hop // 2 if canonical == 'BTC' else node.hop)
                if depth == start_hop and (frontier_ids is None or node.id in frontier_ids):
                    self.queue.append((*key, depth))
                elif depth < start_hop:
                    visited.add(key)
        for edge in result['edges']:
            if edge.edge_type == 'BITCOIN_TRANSACTION_OUTPUT' and edge.target in by_id:
                index = edge.metadata.get('output_index')
                if type(index) is int:
                    self.outpoints.setdefault(by_id[edge.target].address, set()).add((edge.tx_hash, index))
            if edge.edge_type == 'ONCHAIN_TRANSFER' and edge.source in by_id and edge.target in by_id:
                canonical = normalize_chain(edge.chain or by_id[edge.source].chain)
                self.seen[self._fingerprint(canonical, edge.tx_hash, by_id[edge.source].address, by_id[edge.target].address, edge.crypto_amount, edge.asset)] += 1
        headers = {'X-API-Key': self.settings['CRYPTOAPIS_API_KEY'], 'Accept': 'application/json', 'Content-Type': 'application/json'}
        try:
            async with asyncio.timeout(90 if chain in SLUGS else 60):
                async with httpx.AsyncClient(transport=self.transport, timeout=10, follow_redirects=False, headers=headers) as client:
                    while self.queue:
                        current, address, depth = self.queue.popleft()
                        visit = (current, address, tuple(sorted(self.outpoints.get(address, set())))) if current == 'BTC' else (current, address)
                        if visit in visited or depth >= max_hops:
                            continue
                        if len(result['nodes']) >= self.MAX_NODES or len(result['edges']) >= self.MAX_EDGES:
                            self.truncated = True
                            break
                        visited.add(visit)
                        if current == 'BTC':
                            await self._bitcoin(client, address, depth)
                        else:
                            await self._account(client, current, address, depth)
        except _ContinuationStopped as exc:
            stopped = exc
            self.truncated = True
        except TimeoutError:
            stopped = _ContinuationStopped('TIMEOUT', 'Waktu kelanjutan Crypto APIs habis; hasil yang tersedia tetap ditampilkan.')
            self.truncated = True
        finally:
            metadata = result['metadata']
            metadata.update(max_hops=max_hops, cryptoapis_requests_made=metadata.get('cryptoapis_requests_made', 0) + self.requests,
                cryptoapis_mode='FREE', cryptoapis_page_size=self.PAGE_SIZE, cryptoapis_request_limit=self.request_limit,
                cryptoapis_credit_limit=self.MAX_CREDITS_PER_SCAN,
                cryptoapis_credits_consumed=metadata.get('cryptoapis_credits_consumed', 0) + self.credits_consumed,
                cryptoapis_credits_estimated=metadata.get('cryptoapis_credits_estimated', 0) + self.credits_estimated,
                cryptoapis_credits_available=self.credits_available,
                cryptoapis_history_window_days=1 if chain == 'SOLANA' else 14,
                requests_made=metadata.get('requests_made', 0) + self.requests,
                truncated=metadata.get('truncated', False) or self.truncated)
        metadata = result['metadata']
        if stopped:
            metadata.update(partial=True, continuation_status=stopped.reason, cryptoapis_stop_reason=str(stopped),
                cryptoapis_http_status=stopped.http_status, retry_after_seconds=stopped.retry_after)
            result['warnings'].append(str(stopped))
        if self.success_reads:
            providers = list(dict.fromkeys([*metadata.get('provider', '').split(', '), 'CRYPTOAPIS']))
            metadata['provider'] = metadata['scanner_provider'] = ', '.join(filter(None, providers))
            metadata['activity'] = [*metadata.get('activity', []), *self.activity]
            days = metadata['cryptoapis_history_window_days']
            result['warnings'].append(f'Crypto APIs hop 4–{max_hops}: sampel terbaru hingga {days} hari, bukan seluruh riwayat.')
            if chain in SLUGS:
                result['warnings'].append('Token Crypto APIs tanpa logIndex: deduplikasi memakai fingerprint dan urutan kejadian; bukan bukti log lengkap.')
            if chain == 'SOLANA':
                result['warnings'].append('Crypto APIs Solana menampilkan nativeMovements; status eksekusi tidak disediakan endpoint ini. SPL tidak dicakup.')
        if self.truncated:
            result['warnings'].append('HASIL TERBATAS: batas Crypto APIs request/node/edge tercapai sebelum semua hop selesai.')
        if auto_scan_cross_chain and self.requests:
            result['warnings'].append('Deteksi kandidat bridge otomatis berlaku pada fase scanner utama; fase Crypto APIs melanjutkan chain wallet frontier tanpa mengklaim settlement baru.')
        result['pakd_api_exchange_logs'] = self.pakd_consumer.logs
        return result
