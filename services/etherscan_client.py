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
from services.chains import CHAIN_CONFIG, normalize_chain, explorer_address_url, explorer_tx_url

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
# Verified deployment references; chain-scoped labels are context, not settlement proof.
KNOWN_BRIDGE_CONTRACTS = {"ETHEREUM": {
 "0x8731d54e9d02c286767d56ac03e8037c07e01e98": {"name":"Stargate Finance (LayerZero)","source_url":"https://stargateprotocol.gitbook.io/stargate/developers/contract-addresses/mainnet"},
 "0x5427fefa711eff984124bfbb1ab6fbf5e3da1820": {"name":"Celer cBridge","source_url":"https://cbridge-docs.celer.network/reference/contract-addresses"},
 "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5": {"name":"Across SpokePool","source_url":"https://docs.across.to/chains-and-contracts"},
 "0x80c67432656d59144ceff962e8faf8926599bcf8": {"name":"Orbiter Maker","source_url":"https://docs.orbiter.finance/","role":"MAKER_ADDRESS"},
 "0xa0c68c638235ee32657e8f720a23cec1bfc77c77": {"name":"Polygon PoS Bridge","source_url":"https://docs.polygon.technology/pos/reference/contracts/genesis-contracts/"},
 "0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f": {"name":"Arbitrum Official Bridge","source_url":"https://docs.arbitrum.io/build-decentralized-apps/reference/contract-addresses"},
 "0x3ee18b2214aff97000d974cf647e7c347e8fa585": {"name":"Wormhole Token Bridge","source_url":"https://wormhole.com/docs/products/reference/contract-addresses/"},
 "0x1a44076050125825900e736c501f859c50fe728c": {"name":"LayerZero V2 Endpoint","source_url":"https://docs.layerzero.network/v2/deployments/deployed-contracts","pivot_supported":False}
}}
for _chain, _address in {
    "ARBITRUM":"0xe35e9842fceaca96570b734083f4a58e8f7c5f2a",
    "BASE":"0x09aea4b2242abc8bb4bb78d537a67a245a7bec64",
    "OPTIMISM":"0x6f26bf09b1c792e3228e5467807a900a503c0281",
    "POLYGON":"0x9295ee1d8c5b022be115a2ad3c30c72e34e7f096",
    "BSC":"0x4e8e101924ede233c13e2d8622dc8aed2872d505"
}.items():
    KNOWN_BRIDGE_CONTRACTS[_chain] = {_address:{"name":"Across SpokePool","source_url":"https://docs.across.to/chains-and-contracts"}}

CHAIN_TOKENS = {
 "ETHEREUM": {"USDT":(TOKEN_CONTRACTS["USDT"],6),"USDC":(TOKEN_CONTRACTS["USDC"],6)},
 "BSC":{"USDT":("0x55d398326f99059ff775485246999027b3197955",18),"USDC":("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",18)},
 "ARBITRUM":{"USDT":("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",6),"USDC":("0xaf88d065e77c8cc2239327c5edb3a432268e5831",6)},
 "POLYGON":{"USDT":("0xc2132d05d31c914a87c6611c10748aeb04b58e8f6",6),"USDC":("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",6)},
 "OPTIMISM":{"USDT":("0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",6),"USDC":("0x0b2c639c533813f4aa9d7837caf62653d097ff85",6)},
 "BASE":{"USDC":("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",6)}
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
    keys = {"ETHERSCAN_API_KEY", "ETHERSCAN_BASE_URL", "ETHERSCAN_FALLBACK_EXPLORER_URL", "SOLSCAN_API_KEY", "SOLSCAN_BASE_URL"}
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
    MAX_REQUESTS = 36
    MAX_NODES = 64
    MAX_EDGES = 128
    PAGE_SIZE = 15

    def __init__(self, api_key=None, base_url=None, fallback_url=None, transport=None, pakd_consumer=None):
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
        from services.pakd_api_consumer import PAKDAPIConsumer
        self.pakd_consumer = pakd_consumer or PAKDAPIConsumer()

    async def _get(self, client, action, address, chain="ETHEREUM", **extra):
        chain = normalize_chain(chain)
        endpoint = self.base_url if self.api_key else self.fallback_url if chain == "ETHEREUM" else CHAIN_CONFIG[chain]["fallback_url"]
        if not endpoint or chain == "TRON":
            raise EtherscanAPIError("Chain ini memerlukan API key Etherscan atau hanya tersedia sebagai skenario")
        if urlparse(endpoint).path == "/api" and self.api_key and chain != "ETHEREUM":
            raise EtherscanAPIError("Multichain memerlukan endpoint Etherscan V2")
        params = {"module": "account", "action": action, "address": address, **extra}
        if self.provider == "ETHERSCAN":
            params["apikey"] = self.api_key
            if urlparse(endpoint).path == "/v2/api":
                params["chainid"] = str(CHAIN_CONFIG[chain]["chain_id"])
        await _throttle()
        try:
            async with client.stream("GET", endpoint, params=params) as response:
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

    async def _node(self, address, hop, chain="ETHEREUM"):
        chain = normalize_chain(chain)
        match = await self.pakd_consumer.consume_pakd_wallet_reverse_lookup_api(address, chain)
        known = KNOWN_EVM_ENTITIES.get(address) if chain == "ETHEREUM" else None
        bridge = KNOWN_BRIDGE_CONTRACTS.get(chain, {}).get(address)
        label = known["label"] if known else address[:10] + "…" + address[-4:]
        metadata = {"simulation":False,"is_live":True,"evidence_source":self.provider+"_LIVE",
            "ownership":"UNKNOWN","taint_status":"UNKNOWN","balance_status":"NOT_FETCHED",
            "balance_asset":CHAIN_CONFIG[chain]["native_symbol"],"is_seed_wallet":hop==0}
        if match:
            metadata.update(mirror_match=match,identity_source="SYNTHETIC_PAKD_SCOPED_MIRROR_REST_API")
            label=f"{match['pakd_name']} · {match['user_uid']} (API demo)"
        if bridge:metadata["bridge_reference"]=bridge;label="🌉 "+bridge["name"]
        return TrailNode(id=chain.lower()+"-"+address[2:],label=label,node_zone="ONCHAIN_WALLET",
            node_type="BRIDGE_PROTOCOL" if bridge else "PAKD_MIRROR_MATCH" if match else known["kind"] if known else "LIVE_UNATTRIBUTED_WALLET",
            entity_label=label,address=address,chain=chain,chain_id=CHAIN_CONFIG[chain]["chain_id"],
            asset=CHAIN_CONFIG[chain]["native_symbol"],hop=hop,taint_score_pct=None,status="LIVE_READ_ONLY",metadata=metadata,
            is_bridge_node=bool(bridge),bridge_protocol_name=bridge["name"] if bridge else None,
            explorer_url=explorer_address_url(chain,address))

    def _parse(self, rows, action, address, token_filter, direction, chain="ETHEREUM"):
        tokens = CHAIN_TOKENS[normalize_chain(chain)]
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
                    symbol, decimals = CHAIN_CONFIG[normalize_chain(chain)]["native_symbol"], 18
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
                    canonical = next((name for name, (known_contract, _) in tokens.items() if known_contract == contract), None)
                    if canonical:
                        if decimals != tokens[canonical][1]:
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
                if token_filter != "ALL" and symbol != token_filter and not (token_filter == "ETH" and action == "txlist"):
                    continue
                amount = _units(raw["value"], decimals)
                if amount <= 0:
                    continue
                transfers.append({"key": event_key, "from": sender, "to": recipient, "amount": amount, "symbol": symbol,
                                  "hash": tx_hash, "timestamp": timestamp, "contract": contract, "log_index": event_index})
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                skipped += 1
        return transfers, skipped

    async def pivot_across_chains(self, wallet_address, source_chain, amount, timestamp, *, fetch, token_filter="ALL", token_symbol=None):
        candidates=[]
        for chain in CHAIN_CONFIG:
            if CHAIN_CONFIG[chain]["chain_family"] != "EVM" or chain == source_chain:continue
            if not self.api_key and not CHAIN_CONFIG[chain]["fallback_url"]:continue
            actions=["txlist"] if token_filter=="ETH" else ["tokentx"] if token_filter in TOKEN_CONTRACTS else ["txlist","tokentx"]
            for action in actions:
                rows=await fetch(chain,action,wallet_address,False)
                transfers,_=self._parse(rows,action,wallet_address,token_filter,"BOTH",chain)
                for tx in transfers:
                    delay=(tx["timestamp"]-timestamp).total_seconds()
                    if tx["to"]==wallet_address and 0 <= delay <= 7200 and (token_symbol is None or tx["symbol"] == token_symbol) and D("0.8") * amount <= tx["amount"] <= D("1.05") * amount:
                        candidates.append({"chain":chain,"transfer":tx})
                        break
                if any(item["chain"]==chain for item in candidates):break
        return candidates

    async def detect_and_follow_bridge_hop(self,wallet_address,source_chain,amount,timestamp,*,bridge_address,fetch,token_filter="ALL",token_symbol=None):
        bridge=KNOWN_BRIDGE_CONTRACTS.get(source_chain,{}).get(bridge_address)
        if not bridge or bridge.get("pivot_supported") is False:return []
        return await self.pivot_across_chains(wallet_address,source_chain,amount,timestamp,fetch=fetch,token_filter=token_filter,token_symbol=token_symbol)

    async def trace_real_wallet_on_etherscan(self,seed_wallet_address,max_hops=2,token_filter="ALL",direction="OUTBOUND",source_chain="ETHEREUM",auto_scan_cross_chain=False):
        if not isinstance(seed_wallet_address,str) or not ADDRESS.fullmatch(seed_wallet_address):raise ValueError("Alamat EVM tidak valid")
        if type(max_hops) is not int or not 1<=max_hops<=3:raise ValueError("max_hops harus 1 sampai 3")
        if token_filter not in {"ALL","ETH","USDT","USDC"} or direction not in {"OUTBOUND","BOTH"}:raise ValueError("Filter/arah tidak didukung")
        source_chain=normalize_chain(source_chain)
        if CHAIN_CONFIG[source_chain]["chain_family"] != "EVM":raise ValueError("Gunakan scanner khusus untuk jaringan non-EVM")
        seed=seed_wallet_address.lower();nodes={};edges=[];seen=set();visited=set();queue=deque([(source_chain,seed,0)])
        warnings=["LIVE READ ONLY: transaksi publik bukan bukti kepemilikan, taint atau recovery.",
            "Sampel 15 record terbaru per jenis/wallet; internal transfers, NFT, dan saldo ERC-20 tidak dicakup.",
            "Identitas dari Scoped Mirror API PAKD adalah seed sintetis."]
        stats={"requests":0,"truncated":False};cache={};failed=set();pivoted=set()
        async def ensure(chain,address,hop):
            key=(chain,address)
            if key not in nodes:
                if len(nodes)>=self.MAX_NODES:stats["truncated"]=True;return None
                nodes[key]=await self._node(address,hop,chain)
            return nodes[key]
        async def fetch(chain,action,address,required=False):
            key=(chain,action,address)
            if key in cache:return cache[key]
            if stats["requests"]>=self.MAX_REQUESTS:stats["truncated"]=True;return [] if action!="balance" else None
            if chain in failed:return [] if action!="balance" else None
            params={"tag":"latest"} if action=="balance" else {"page":1,"offset":self.PAGE_SIZE,"sort":"desc"}
            if action=="txlist":params.update(startblock=0,endblock=99999999)
            if action=="tokentx" and token_filter in CHAIN_TOKENS[chain]:params["contractaddress"]=CHAIN_TOKENS[chain][token_filter][0]
            stats["requests"]+=1
            try:
                value=await self._get(client,action,address,chain=chain,**params)
            except EtherscanAPIError:
                if required:raise
                failed.add(chain);warnings.append(f"{chain}: explorer tidak tersedia/kuota dibatasi; chain ini belum dapat diperiksa.")
                return [] if action!="balance" else None
            cache[key]=value
            if isinstance(value,list) and len(value)>=self.PAGE_SIZE:stats["truncated"]=True
            return value
        async def add_transfer(chain,tx,depth):
            event=chain+":"+tx["key"]
            if event in seen:return None
            if len(edges)>=self.MAX_EDGES:stats["truncated"]=True;return None
            a=await ensure(chain,tx["from"],depth);b=await ensure(chain,tx["to"],depth+1)
            if not a or not b:return None
            seen.add(event)
            edge=TrailEdge(id="live-"+sha256(event.encode()).hexdigest()[:24],source=a.id,target=b.id,edge_type="ONCHAIN_TRANSFER",
                label=f"{tx['amount']} {tx['symbol']}",crypto_amount=tx["amount"],asset=tx["symbol"],tx_hash=tx["hash"],timestamp=tx["timestamp"],
                chain=chain,chain_id=CHAIN_CONFIG[chain]["chain_id"],explorer_url=explorer_tx_url(chain,tx["hash"]),
                metadata={"simulation":False,"is_live":True,"evidence_source":self.provider+"_LIVE","chain":chain,"contract_address":tx["contract"],
                    "log_index":tx["log_index"],"taint_status":"UNKNOWN"})
            edges.append(edge)
            if tx["contract"] and tx["log_index"] is None and not any("logIndex" in w for w in warnings):
                warnings.append("Provider tanpa logIndex: deduplikasi ERC-20 memakai fingerprint dan urutan baris, bukan bukti log lengkap.")
            return edge
        try:
            async with asyncio.timeout(90):
                async with httpx.AsyncClient(transport=self.transport,timeout=10,follow_redirects=False) as client:
                    while queue:
                        chain,address,depth=queue.popleft();key=(chain,address)
                        if key in visited or depth>=max_hops:continue
                        if stats["requests"]>=self.MAX_REQUESTS:stats["truncated"]=True;break
                        node=await ensure(chain,address,depth)
                        if not node:continue
                        visited.add(key)
                        balance=await fetch(chain,"balance",address,chain==source_chain and address==seed)
                        if balance is not None:node.balance_crypto=balance;node.metadata.update(balance_status="FETCHED",balance_eth=str(balance))
                        actions=["txlist"] if token_filter=="ETH" else ["tokentx"] if token_filter in TOKEN_CONTRACTS else ["txlist","tokentx"]
                        for action in actions:
                            if token_filter in TOKEN_CONTRACTS and token_filter not in CHAIN_TOKENS[chain]:
                                warnings.append(f"{chain}: kontrak {token_filter} kanonis belum didukung.");continue
                            rows=await fetch(chain,action,address,chain==source_chain and address==seed)
                            transfers,skipped=self._parse(rows,action,address,token_filter,direction,chain)
                            if skipped:warnings.append(f"{chain}: {skipped} record tidak valid dilewati.")
                            for tx in transfers:
                                edge=await add_transfer(chain,tx,depth)
                                counterpart=tx["to"] if tx["from"]==address else tx["from"]
                                if edge:queue.append((chain,counterpart,depth+1))
                                bridge=KNOWN_BRIDGE_CONTRACTS.get(chain,{}).get(counterpart)
                                if auto_scan_cross_chain and tx["from"]==address and bridge and (chain,tx["hash"]) not in pivoted:
                                    pivoted.add((chain,tx["hash"]))
                                    candidates=await self.detect_and_follow_bridge_hop(address,chain,tx["amount"],tx["timestamp"],bridge_address=counterpart,fetch=fetch,token_filter=token_filter,token_symbol=tx["symbol"])
                                    for candidate in candidates[:5]:
                                        destination=candidate["chain"];landing=candidate["transfer"]
                                        target=await ensure(destination,address,depth+1)
                                        bridge_node=await ensure(chain,counterpart,depth+1)
                                        if bridge_node:
                                            bridge_node.bridged_from_chain=chain;bridge_node.bridged_to_chain=destination
                                        if not target or not bridge_node or len(edges)>=self.MAX_EDGES:stats["truncated"]=True;continue
                                        target.bridged_from_chain=chain;target.bridged_to_chain=destination
                                        pivotid=f"{chain}:{tx['hash']}:{destination}:{landing['key']}"
                                        edges.append(TrailEdge(id="pivot-"+sha256(pivotid.encode()).hexdigest()[:24],source=bridge_node.id,target=target.id,
                                            edge_type="CROSS_CHAIN_BRIDGE",label=f"KANDIDAT BRIDGE: {chain} → {destination}",chain=destination,chain_id=CHAIN_CONFIG[destination]["chain_id"],
                                            bridge_protocol_name=bridge["name"],bridged_from_chain=chain,bridged_to_chain=destination,
                                            timestamp=landing["timestamp"],explorer_url=explorer_tx_url(destination,landing["hash"]),
                                            metadata={"simulation":False,"correlation_status":"CANDIDATE_UNVERIFIED","source_tx_hash":tx["hash"],"destination_tx_hash":landing["hash"],
                                                "source_chain":chain,"destination_chain":destination,
                                                "source_explorer_url":explorer_tx_url(chain,tx["hash"]),"destination_explorer_url":explorer_tx_url(destination,landing["hash"]),
                                                "amount_source":str(tx["amount"]),"amount_destination":str(landing["amount"]),"evidence_source":"TEMPORAL_ADDRESS_CORRELATION", "is_live":False}))
                                        await add_transfer(destination,landing,depth)
                                        queue.appendleft((destination,address,0))
                    if auto_scan_cross_chain:
                        for chain in CHAIN_CONFIG:
                            if CHAIN_CONFIG[chain]["chain_family"] != "EVM" or chain == source_chain or not self.api_key and not CHAIN_CONFIG[chain]["fallback_url"]:continue
                            action="tokentx" if token_filter in TOKEN_CONTRACTS else "txlist"
                            rows=await fetch(chain,action,seed)
                            txs,_=self._parse(rows,action,seed,token_filter,"BOTH",chain)
                            if txs:
                                await ensure(chain,seed,0)
                                for tx in txs:await add_transfer(chain,tx,0)
        except TimeoutError:raise EtherscanAPIError("Deadline multichain tercapai; kurangi hop/chain") from None
        if stats["truncated"]:warnings.append("HASIL TERBATAS: halaman atau budget request/node/edge tercapai.")
        if not edges:warnings.append("Tidak ada transfer sesuai filter pada halaman yang tersedia.")
        if not self.api_key:warnings.append("Fallback publik per chain; BSC memerlukan key Etherscan, TRON hanya simulasi.")
        warnings.append("Pivot lintas-chain adalah kandidat alamat/waktu dalam 2 jam; belum bukti settlement bridge dan tidak menambah recovery.")
        return {"nodes":list(nodes.values()),"edges":edges,"warnings":warnings,"pakd_api_exchange_logs":self.pakd_consumer.logs,
            "metadata":{"provider":self.provider,"network":"MULTICHAIN_MAINNET","chain_id":CHAIN_CONFIG[source_chain]["chain_id"],"source_chain":source_chain,
                "seed_wallet_address":seed,"simulation":False,"max_hops":max_hops,"token_filter":token_filter,"native_asset":CHAIN_CONFIG[source_chain]["native_symbol"],
                "direction":direction,"auto_scan_cross_chain":auto_scan_cross_chain,"requests_made":stats["requests"],"wallets_queried":len(visited),
                "truncated":stats["truncated"],"unavailable_chains":sorted(failed),"fetched_at":datetime.now(timezone.utc).isoformat()}}
