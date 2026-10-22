"""Bounded, read-only Ethereum Mainnet transfers through Etherscan or Blockscout."""
import asyncio
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse

import httpx

from schemas import TrailEdge, TrailNode

D = Decimal
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
TOKEN_CONTRACTS = {
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
}
# Public labels are context only; they do not identify an exchange's customer.
KNOWN_EVM_ENTITIES = {
    TOKEN_CONTRACTS["USDT"]: {"label": "Tether: USDT Contract", "kind": "TOKEN_CONTRACT", "source_url": "https://tether.to/en/supported-protocols/"},
    TOKEN_CONTRACTS["USDC"]: {"label": "Circle: USDC Contract", "kind": "TOKEN_CONTRACT", "source_url": "https://developers.circle.com/stablecoins/usdc-contract-addresses"},
    "0x28c6c06298d514db089934071355e5743bf21d60": {"label": "Binance 14 (public label)", "kind": "PUBLIC_EXCHANGE_WALLET", "source_url": "https://etherscan.io/address/0x28c6c06298d514db089934071355e5743bf21d60"},
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": {"label": "Uniswap V2 Router 02", "kind": "DEX_ROUTER", "source_url": "https://github.com/Uniswap/v2-periphery"},
}
_rate_lock = threading.Lock()
_next_request_at = 0.0
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


async def _throttle():
    global _next_request_at
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_request_at)
        # Stay below the free tier and historical endpoint limits.
        _next_request_at = slot + 0.51
    await asyncio.sleep(max(0, slot - now))


class EtherscanAPIError(RuntimeError):
    """Safe error message, without provider bodies or URLs containing credentials."""


def _config():
    keys = {"ETHERSCAN_API_KEY", "ETHERSCAN_BASE_URL", "ETHERSCAN_FALLBACK_EXPLORER_URL"}
    values = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = raw.strip().removeprefix("export ").partition("=")
            if sep and key.strip() in keys:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
    return {key: os.environ.get(key, values.get(key, "")) for key in keys}


def _validated_url(value, fallback=False):
    parsed = urlparse(value)
    valid = (parsed.hostname, parsed.path.rstrip("/")) in (
        {("eth.blockscout.com", "/api")} if fallback else
        {("api.etherscan.io", "/v2/api"), ("api.etherscan.io", "/api")}
    )
    if not valid or parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.query or parsed.fragment:
        raise ValueError("URL explorer harus endpoint HTTPS resmi Ethereum Mainnet tanpa kredensial atau query")
    return value.rstrip("/")


def _uint(value):
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,78}", text):
        raise ValueError("Nominal integer tidak valid")
    result = int(text)
    if result >= 2**256:
        raise ValueError("Nominal melebihi uint256")
    return result


def _units(value, decimals):
    with localcontext() as context:
        context.prec = 100
        return D(_uint(value)).scaleb(-decimals)


class EtherscanClient:
    MAX_REQUESTS = 24
    MAX_NODES = 64
    MAX_EDGES = 128
    PAGE_SIZE = 15

    def __init__(self, api_key=None, base_url=None, fallback_url=None, transport=None, mirror_db=None):
        config = _config()
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("API key Etherscan wajib teks")
        self.api_key = ((api_key or "").strip() or config["ETHERSCAN_API_KEY"]).strip()
        if len(self.api_key) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in self.api_key):
            raise ValueError("Format API key Etherscan tidak valid")
        self.base_url = _validated_url(base_url or config["ETHERSCAN_BASE_URL"] or "https://api.etherscan.io/v2/api")
        self.fallback_url = _validated_url(fallback_url or config["ETHERSCAN_FALLBACK_EXPLORER_URL"] or "https://eth.blockscout.com/api", fallback=True)
        self.provider = "ETHERSCAN" if self.api_key else "BLOCKSCOUT"
        self.endpoint = self.base_url if self.api_key else self.fallback_url
        self.transport = transport
        if mirror_db is None:
            from services.pakd_mirror_db import mirror_db as shared_mirror_db
            mirror_db = shared_mirror_db
        self.mirror_db = mirror_db

    async def _get(self, client, action, address, **extra):
        params = {"module": "account", "action": action, "address": address, **extra}
        if self.provider == "ETHERSCAN":
            params["apikey"] = self.api_key
            if urlparse(self.endpoint).path == "/v2/api":
                params["chainid"] = "1"
        await _throttle()
        try:
            async with client.stream("GET", self.endpoint, params=params) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise EtherscanAPIError("Respons explorer melebihi batas aman; kurangi cakupan pelacakan")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
            result = payload.get("result")
            empty_messages = {"no transactions found", "no token transfers found"}
            if str(payload.get("status")) == "0" and action != "balance" and (
                result == [] and str(payload.get("message", "")).lower() in empty_messages
                or isinstance(result, str) and result.lower() in empty_messages
            ):
                return []
            if str(payload.get("status")) != "1":
                raise EtherscanAPIError("Explorer menolak permintaan; periksa API key, kuota, atau ketersediaan layanan")
            if action == "balance":
                return _units(result, 18)
            if not isinstance(result, list):
                raise ValueError()
            return result
        except EtherscanAPIError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise EtherscanAPIError("Explorer Mainnet gagal diakses atau respons tidak valid; tidak ada data simulasi pengganti") from None

    def _node(self, address, hop):
        match = self.mirror_db.match_wallet_in_mirror_db(address)
        known = KNOWN_EVM_ENTITIES.get(address)
        metadata = {"simulation": False, "is_live": True, "evidence_source": self.provider + "_LIVE",
                    "architecture_zone": "Zona 3: Ethereum Mainnet", "ownership": "UNKNOWN", "taint_status": "UNKNOWN",
                    "balance_status": "NOT_FETCHED", "balance_asset": "ETH", "is_seed_wallet": hop == 0,
                    "explorer_url": "https://etherscan.io/address/" + address}
        label, kind = address[:10] + "…" + address[-4:], "LIVE_UNATTRIBUTED_WALLET"
        if known:
            label, kind = known["label"], known["kind"]
            metadata["public_entity"] = known
        if match:
            metadata.update(mirror_match=match, identity_source="SYNTHETIC_PAKD_MIRROR_DB_SQLITE", ownership="MIRROR_RECORD_MATCH_REQUIRES_VERIFICATION")
            label, kind = f"{match['pakd_name']} · {match['user_uid']} (mirror demo)", "PAKD_MIRROR_MATCH"
        return TrailNode(id="eth-" + address[2:], label=label, node_zone="ONCHAIN_WALLET", node_type=kind,
                         entity_label=label if known or match else "Unattributed Mainnet Address", address=address,
                         chain="ETHEREUM_ERC20", asset="ETH", hop=hop, taint_score_pct=None,
                         status="LIVE_MIRROR_MATCH" if match else "LIVE_READ_ONLY", metadata=metadata)

    def _parse(self, rows, action, address, token_filter, direction):
        transfers, skipped = [], 0
        occurrences = Counter()
        for raw in rows[:self.PAGE_SIZE]:
            try:
                if not isinstance(raw, dict):
                    raise ValueError()
                sender, recipient, tx_hash = raw.get("from"), raw.get("to"), raw.get("hash")
                if not all(isinstance(item, str) and regex.fullmatch(item) for item, regex in ((sender, ADDRESS), (recipient, ADDRESS), (tx_hash, TX_HASH))):
                    raise ValueError()
                sender, recipient, tx_hash = sender.lower(), recipient.lower(), tx_hash.lower()
                if sender != address and (direction != "BOTH" or recipient != address):
                    continue
                if str(raw.get("isError", "0")) != "0" or str(raw.get("txreceipt_status", "1")) != "1":
                    continue
                timestamp = datetime.fromtimestamp(_uint(raw["timeStamp"]), timezone.utc)
                if timestamp > datetime.now(timezone.utc) or timestamp.year < 2015:
                    raise ValueError()
                contract, event_index = None, None
                if action == "txlist":
                    if "isError" not in raw:
                        raise ValueError()
                    symbol, decimals = "ETH", 18
                    event_key = tx_hash + ":native"
                else:
                    contract = str(raw.get("contractAddress", "")).lower()
                    if not ADDRESS.fullmatch(contract):
                        raise ValueError()
                    symbol = str(raw.get("tokenSymbol", ""))
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,20}", symbol):
                        raise ValueError()
                    decimals = _uint(raw.get("tokenDecimal"))
                    if decimals > 36:
                        raise ValueError()
                    canonical = next((name for name, known_contract in TOKEN_CONTRACTS.items() if known_contract == contract), None)
                    if canonical:
                        if decimals != 6:
                            raise ValueError()
                        symbol = canonical
                    elif symbol.upper() in TOKEN_CONTRACTS:
                        raise ValueError()
                    fingerprint = f"{tx_hash}:{contract}:{sender}:{recipient}:{raw['value']}"
                    event_index = raw.get("logIndex")
                    if event_index is not None:
                        event_index = str(_uint(event_index))
                        event_key = f"{tx_hash}:{contract}:log:{event_index}"
                    else:
                        occurrences[fingerprint] += 1
                        event_key = f"{fingerprint}:occurrence:{occurrences[fingerprint]}"
                if token_filter != "ALL" and symbol != token_filter:
                    continue
                amount = _units(raw["value"], decimals)
                if amount <= 0:
                    continue
                transfers.append({"key": event_key, "from": sender, "to": recipient, "amount": amount, "symbol": symbol,
                                  "hash": tx_hash, "timestamp": timestamp, "contract": contract, "log_index": event_index})
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                skipped += 1
        return transfers, skipped

    async def trace_real_wallet_on_etherscan(self, seed_wallet_address, max_hops=2, token_filter="ALL", direction="OUTBOUND"):
        if not isinstance(seed_wallet_address, str) or not ADDRESS.fullmatch(seed_wallet_address):
            raise ValueError("Wallet wajib berupa alamat Ethereum 0x dengan 40 digit heksadesimal")
        if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= 3:
            raise ValueError("max_hops harus 1 sampai 3")
        if token_filter not in {"ALL", "ETH", "USDT", "USDC"} or direction not in {"OUTBOUND", "BOTH"}:
            raise ValueError("Filter aset atau arah pelacakan tidak didukung")
        seed = seed_wallet_address.lower()
        nodes = {seed: self._node(seed, 0)}
        queue, visited, edges, seen_events = deque([(seed, 0)]), set(), [], set()
        requests, skipped, truncated, missing_log_index = 0, 0, False, False
        warnings = ["LIVE READ ONLY: transaksi asli Ethereum Mainnet; hubungan antar-wallet bukan bukti kepemilikan, taint dana laporan, atau nilai yang dapat dibekukan.",
                    "Satu halaman 15 transaksi terbaru per jenis dan wallet; bukan riwayat lengkap. Transfer internal, NFT, dan saldo token ERC-20 tidak dicakup.",
                    "Kecocokan identitas Mirror DB pada PoC berasal dari data sintetis, bukan KYC PAKD operasional."]
        if self.provider == "BLOCKSCOUT":
            warnings.append("API key kosong: memakai Blockscout Ethereum Mainnet publik; ketersediaan, indeks, dan kuota layanan tidak dijamin.")
        try:
            async with asyncio.timeout(75):
                async with httpx.AsyncClient(transport=self.transport, timeout=10, follow_redirects=False) as client:
                    while queue:
                        address, depth = queue.popleft()
                        if address in visited or depth >= max_hops:
                            continue
                        actions = ["txlist"] if token_filter == "ETH" else ["tokentx"] if token_filter in TOKEN_CONTRACTS else ["txlist", "tokentx"]
                        if requests + len(actions) + 1 > self.MAX_REQUESTS:
                            truncated = True
                            break
                        visited.add(address)
                        requests += 1
                        balance = await self._get(client, "balance", address, tag="latest")
                        nodes[address].balance_crypto = balance
                        nodes[address].metadata.update(balance_status="FETCHED", balance_eth=str(balance))
                        for action in actions:
                            params = {"page": 1, "offset": self.PAGE_SIZE, "sort": "desc"}
                            if action == "txlist":
                                params.update(startblock=0, endblock=99999999)
                            elif token_filter in TOKEN_CONTRACTS:
                                params["contractaddress"] = TOKEN_CONTRACTS[token_filter]
                            requests += 1
                            rows = await self._get(client, action, address, **params)
                            truncated |= len(rows) >= self.PAGE_SIZE
                            transfers, invalid = self._parse(rows, action, address, token_filter, direction)
                            skipped += invalid
                            for transfer in transfers:
                                if transfer["key"] in seen_events:
                                    continue
                                counterpart = transfer["to"] if transfer["from"] == address else transfer["from"]
                                if counterpart not in nodes and len(nodes) >= self.MAX_NODES or len(edges) >= self.MAX_EDGES:
                                    truncated = True
                                    continue
                                seen_events.add(transfer["key"])
                                if counterpart not in nodes:
                                    nodes[counterpart] = self._node(counterpart, depth + 1)
                                    queue.append((counterpart, depth + 1))
                                missing_log_index |= action == "tokentx" and transfer["log_index"] is None
                                metadata = {"simulation": False, "is_live": True, "evidence_source": self.provider + "_LIVE",
                                            "chain": "ETHEREUM_ERC20", "contract_address": transfer["contract"],
                                            "log_index": transfer["log_index"], "event_identity": "LOG_INDEX" if transfer["log_index"] is not None else "NATIVE_TX_HASH" if action == "txlist" else "PROVIDER_ROW_OCCURRENCE",
                                            "explorer_url": "https://etherscan.io/tx/" + transfer["hash"], "taint_status": "UNKNOWN"}
                                edges.append(TrailEdge(id="live-" + sha256(transfer["key"].encode()).hexdigest()[:24],
                                    source=nodes[transfer["from"]].id, target=nodes[transfer["to"]].id, edge_type="ONCHAIN_TRANSFER",
                                    label=f"{transfer['amount']} {transfer['symbol']}", crypto_amount=transfer["amount"], asset=transfer["symbol"],
                                    tx_hash=transfer["hash"], timestamp=transfer["timestamp"], metadata=metadata))
        except TimeoutError:
            raise EtherscanAPIError("Batas waktu pelacakan Mainnet tercapai; coba 1 hop atau filter satu aset") from None
        if truncated:
            warnings.append("HASIL TERBATAS: batas halaman, jumlah request, node, atau edge tercapai; sebagian riwayat/cabang belum diambil.")
        if missing_log_index:
            warnings.append("Provider tidak menyertakan logIndex untuk sebagian ERC-20; deduplikasi memakai fingerprint dan urutan kemunculan pada halaman provider, bukan bukti log lengkap.")
        if skipped:
            warnings.append(f"Record explorer tidak valid/tidak didukung dilewati: {skipped}.")
        if not edges:
            warnings.append("Tidak ada transfer yang memenuhi filter pada halaman yang tersedia; wallet tetap ditampilkan tanpa membuat transaksi demo.")
        return {"nodes": list(nodes.values()), "edges": edges, "warnings": warnings,
                "metadata": {"provider": self.provider, "network": "ETHEREUM_MAINNET", "chain_id": 1, "simulation": False,
                             "seed_wallet_address": seed, "max_hops": max_hops, "token_filter": token_filter, "direction": direction,
                             "requests_made": requests, "wallets_queried": len(visited), "page_size": self.PAGE_SIZE, "truncated": truncated,
                             "fetched_at": datetime.now(timezone.utc).isoformat()}}
