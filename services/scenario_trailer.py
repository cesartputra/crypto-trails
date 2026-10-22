"""Deterministic scenario graph and proportional demo attribution, separate from live APIs."""
from datetime import datetime
from decimal import Decimal
from schemas import TrailNode, TrailEdge
from scenarios import RATE
from services.pakd_mirror_db import mirror_db

D = Decimal


class ScenarioTrailer:
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
                          "evidence_source": "SIMULATED_SCENARIO_LEDGER", "taint_model": "proportional_chronological_stock"}))
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
            metadata = {**w, "evidence_source": "SIMULATED_SCENARIO", "architecture_zone": "Zona 3: On-Chain Trails · skenario sintetis", "scenario_entity_profile": w["label"],
                        "taint_model": "proportional_chronological_stock", "onchain_hop": hops[wallet_id],
                        "is_initial_wallet": wallet_id == initial, "tainted_balance_crypto": str(balance * fraction),
                        "calculated_remaining_crypto": str(funds[wallet_id]), "cumulative_received_crypto": str(amount),
                        "attribution_status": "STOPPED_AT_TRANSFORMATION" if w.get("boundary") else "FIXTURE_ATTRIBUTED",
                        "tx_hashes": [tx["tx_hash"] for tx in ledger if tx["target"] == wallet_id or tx["source"] == wallet_id]}
            match = mirror_db.match_wallet_in_mirror_db(w["address"], ramp["chain"])
            if match:
                metadata["mirror_match"] = match
            nodes.append(TrailNode(id=wallet_id, label=w["label"], node_zone="ONCHAIN_WALLET", node_type=w["kind"],
                entity_label=w["label"] if w["kind"] != "UNHOSTED_WALLET" else "Unhosted Wallet",
                address=w["address"], chain=ramp["chain"], asset=ramp["asset"], crypto_amount=amount,
                amount_idr=tainted_received[wallet_id] * RATE, balance_crypto=balance,
                taint_score_pct=pct.quantize(D("0.01")), hop=source_hop + 1 + hops[wallet_id], metadata=metadata))
        tx_hash = ramp["withdrawal_tx_hash"]
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
                    metadata={"evidence_source": "SIMULATED_SCENARIO_LEDGER", "attribution_status": "UNATTRIBUTED_POOL_FLOW",
                              "balance_status": "NOT_FETCHED",
                              "note": scenario.get("expansion_note", "Hop investigasi tambahan")}))
            new_edges.append(TrailEdge(id=tx["id"], source=tx["source"], target=tx["target"], edge_type="ONCHAIN_TRANSFER",
                label=f"{tx['crypto_amount']} {tx['asset']} · investigasi", crypto_amount=D(tx["crypto_amount"]), asset=tx["asset"],
                tx_hash=tx["tx_hash"], timestamp=tx["timestamp"], metadata={"attribution_status": "UNATTRIBUTED_POOL_FLOW"}))
        return new_nodes, new_edges
