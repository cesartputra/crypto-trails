"""Read-only Arkham client plus deterministic fixture on-chain attribution."""
import os
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote, urlparse
import httpx
from schemas import TrailNode, TrailEdge
from scenarios import SCENARIOS, RATE

D = Decimal
CHAIN_NAMES = {"TRON_TRC20": "tron", "ETHEREUM_ERC20": "ethereum", "BSC_BEP20": "bsc"}


class ArkhamAPIError(RuntimeError):
    pass


class ArkhamClient:
    def __init__(self, api_key=None, base_url=None, transport=None):
        self.api_key = os.getenv("ARKHAM_API_KEY", "") if api_key is None else api_key
        self.base_url = (base_url or os.getenv("ARKHAM_BASE_URL", "https://api.arkhamintelligence.com")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {"api.arkhamintelligence.com", "api.arkm.com"} or parsed.username or parsed.password:
            raise ValueError("ARKHAM_BASE_URL harus host HTTPS resmi Arkham")
        self.transport = transport
        self.simulation = not bool(self.api_key.strip())

    async def _get(self, path, params):
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers={"API-Key": self.api_key},
                                         timeout=10, transport=self.transport, follow_redirects=False) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ArkhamAPIError("Arkham mengembalikan struktur respons yang tidak didukung")
                return payload
        except (httpx.HTTPError, ValueError) as exc:
            # Credentials and upstream response bodies must not enter API error messages.
            raise ArkhamAPIError("Arkham gagal diakses; periksa otorisasi, kuota, host, atau kontrak API") from exc

    async def get_transfers(self, address, chain):
        if chain not in CHAIN_NAMES:
            raise ValueError("Jaringan tidak didukung")
        if self.simulation:
            for scenario in SCENARIOS.values():
                address_ids = {w["id"] for w in scenario["wallets"] if w["address"] == address and scenario["onramp"]["chain"] == chain}
                if address_ids:
                    return {"transfers": [tx for tx in scenario.get("chain_ledger", []) + scenario.get("expansion_ledger", []) if tx["source"] in address_ids], "simulation": True}
            return {"transfers": [], "simulation": True}
        return await self._get("/transfers", {"base": address, "chains": CHAIN_NAMES[chain], "flow": "out", "limit": 50,
                                              "offset": 0, "sortKey": "time", "sortDir": "asc"})

    async def get_address_intelligence(self, address, chain):
        if chain not in CHAIN_NAMES:
            raise ValueError("Jaringan tidak didukung")
        if self.simulation:
            for scenario in SCENARIOS.values():
                for w in scenario["wallets"]:
                    if w["address"] == address and scenario["onramp"]["chain"] == chain:
                        return {"address": address, "entity": w["label"], "type": w["kind"], "simulation": True}
            return {"address": address, "entity": "Unknown", "simulation": True}
        return await self._get(f"/intelligence/address/{quote(address, safe='')}", {"chain": CHAIN_NAMES[chain]})

    def trace_fixture(self, scenario, nodes, edges):
        """Consume chronological stock proportionally; show cumulative flow separately from balance."""
        ramp = scenario["onramp"]
        wallets = {w["id"]: w for w in scenario["wallets"]}
        initial = ramp["initial_wallet"]
        withdrawal_time = datetime.fromisoformat(ramp["withdrawal_timestamp"])
        incoming = {initial: D(ramp["crypto_amount"])}
        tainted_received = dict(incoming)
        funds = {initial: incoming[initial] + D(wallets[initial].get("clean_balance_crypto", 0))}
        taint_stock = {initial: incoming[initial]}
        hops = {initial: 0}
        source_hop = next(n.hop for n in nodes if n.id == "scammer-uid")
        ledger = sorted(scenario["chain_ledger"], key=lambda x: datetime.fromisoformat(x["timestamp"]))
        if len({tx["id"] for tx in ledger}) != len(ledger):
            raise ValueError("Duplikat transfer on-chain dalam ledger")
        for tx in ledger:
            if datetime.fromisoformat(tx["timestamp"]) < withdrawal_time:
                raise ValueError("Transfer on-chain mendahului initial withdrawal")
            source, target = tx["source"], tx["target"]
            if source not in incoming or hops[source] >= 3:
                continue
            amount = D(tx["crypto_amount"])
            if not amount.is_finite() or amount <= 0 or amount > funds[source]:
                raise ValueError("Transfer on-chain melebihi saldo yang tersedia atau nominal tidak valid")
            fraction = taint_stock[source] / funds[source]
            tainted = amount * fraction
            funds[source] -= amount
            taint_stock[source] -= tainted
            if target not in funds:
                funds[target] = D(wallets[target].get("clean_balance_crypto", 0))
                taint_stock[target] = D(0)
            funds[target] += amount
            taint_stock[target] += tainted
            incoming[target] = incoming.get(target, D(0)) + amount
            tainted_received[target] = tainted_received.get(target, D(0)) + tainted
            hops[target] = min(hops.get(target, 99), hops[source] + 1)
            edges.append(TrailEdge(id=tx["id"], source=source, target=target, edge_type="ONCHAIN_TRANSFER",
                label=f"{amount:,.4f}".rstrip("0").rstrip(".") + f" {tx['asset']}", crypto_amount=amount,
                asset=tx["asset"], amount_idr=tainted * RATE, tx_hash=tx["tx_hash"], timestamp=tx["timestamp"],
                metadata={"chain": tx["chain"], "tainted_crypto_amount": str(tainted),
                          "evidence_source": "SYNTHETIC_ARKHAM_LEDGER", "taint_model": "proportional_chronological_stock"}))
        for wallet_id, spent in scenario.get("offramp_crypto_spent", {}).items():
            spent = D(spent)
            if spent < 0 or spent > funds.get(wallet_id, D(0)):
                raise ValueError("Off-ramp melebihi saldo deposit")
            fraction = taint_stock[wallet_id] / funds[wallet_id] if funds[wallet_id] else D(0)
            funds[wallet_id] -= spent
            taint_stock[wallet_id] -= spent * fraction
        for wallet_id, amount in incoming.items():
            w = wallets[wallet_id]
            clean = D(w.get("clean_balance_crypto", 0))
            balance = D(w["balance_crypto"])
            if balance < 0 or balance > funds[wallet_id]:
                raise ValueError("Saldo wallet tidak konsisten dengan ledger")
            if funds[wallet_id]:
                fraction = taint_stock[wallet_id] / funds[wallet_id]
            else:
                fraction = tainted_received[wallet_id] / (amount + clean) if amount + clean else D(0)
            pct = min(fraction * 100, D(100))
            metadata = {**w, "evidence_source": "SYNTHETIC_ARKHAM_INTELLIGENCE", "arkham_entity_profile": w["label"],
                        "taint_model": "proportional_chronological_stock", "onchain_hop": hops[wallet_id],
                        "is_initial_wallet": wallet_id == initial, "tainted_balance_crypto": str(balance * fraction),
                        "calculated_remaining_crypto": str(funds[wallet_id]), "cumulative_received_crypto": str(amount),
                        "attribution_status": "STOPPED_AT_TRANSFORMATION" if w.get("boundary") else "FIXTURE_ATTRIBUTED",
                        "tx_hashes": [tx["tx_hash"] for tx in ledger if tx["target"] == wallet_id or tx["source"] == wallet_id]}
            nodes.append(TrailNode(id=wallet_id, label=w["label"], node_zone="ONCHAIN_WALLET", node_type=w["kind"],
                entity_label=w["label"] if w["kind"] != "UNHOSTED_WALLET" else "Unhosted Wallet",
                address=w["address"], chain=ramp["chain"], asset=ramp["asset"], crypto_amount=amount,
                amount_idr=tainted_received[wallet_id] * RATE, balance_crypto=balance,
                taint_score_pct=pct.quantize(D("0.01")), hop=source_hop + 1 + hops[wallet_id], metadata=metadata))
        tx_hash = ("0x" if ramp["chain"] != "TRON_TRC20" else "") + __import__("hashlib").sha256((scenario["id"] + "-withdrawal").encode()).hexdigest()
        next(n for n in nodes if n.id == initial).metadata["tx_hashes"].insert(0, tx_hash)
        edges.append(TrailEdge(id="initial-withdrawal", source="scammer-uid", target=initial, edge_type="EXCHANGE_WITHDRAWAL",
            label=f"Withdraw {ramp['crypto_amount']} {ramp['asset']}", crypto_amount=D(ramp["crypto_amount"]), asset=ramp["asset"],
            amount_idr=D(ramp["crypto_amount"]) * RATE, tx_hash=tx_hash, timestamp=withdrawal_time,
            metadata={"chain": ramp["chain"], "evidence_source": "SYNTHETIC_EXCHANGE_WITHDRAWAL", "uid": ramp["uid"]}))

    def expand_fixture(self, scenario, address, chain, existing_ids):
        wallets = {w["id"]: w for w in scenario["wallets"]}
        source_ids = {w["id"] for w in scenario["wallets"] if w["address"] == address}
        selected = [tx for tx in scenario.get("expansion_ledger", []) if tx["source"] in source_ids and tx["chain"] == chain]
        new_nodes, new_edges = [], []
        for tx in selected:
            w = wallets[tx["target"]]
            if w["id"] not in existing_ids:
                new_nodes.append(TrailNode(id=w["id"], label=w["label"], node_zone="ONCHAIN_WALLET", node_type=w["kind"],
                    entity_label=w["label"], address=w["address"], chain=chain, asset=tx["asset"],
                    crypto_amount=D(tx["crypto_amount"]), amount_idr=D(0), taint_score_pct=None, hop=9,
                    metadata={"evidence_source": "SYNTHETIC_ARKHAM_LEDGER", "attribution_status": "UNATTRIBUTED_POOL_FLOW",
                              "balance_status": "NOT_FETCHED",
                              "note": scenario.get("expansion_note", "Hop investigasi tambahan")}))
            new_edges.append(TrailEdge(id=tx["id"], source=tx["source"], target=tx["target"], edge_type="ONCHAIN_TRANSFER",
                label=f"{tx['crypto_amount']} {tx['asset']} · investigasi", crypto_amount=D(tx["crypto_amount"]), asset=tx["asset"],
                tx_hash=tx["tx_hash"], timestamp=tx["timestamp"], metadata={"attribution_status": "UNATTRIBUTED_POOL_FLOW"}))
        return new_nodes, new_edges
