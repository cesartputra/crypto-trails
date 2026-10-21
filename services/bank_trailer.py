"""Chronological fiat trailing and explainable merchant candidate scoring."""
from datetime import datetime
from decimal import Decimal
from schemas import TrailEdge, TrailNode

D = Decimal


def merchant_heuristic(account: dict, release_delay_seconds: float | None = None) -> dict:
    """Candidate score is an indicator, never a KYC/ownership determination."""
    incoming = account.get("incoming_24h", 0)
    outgoing = account.get("outgoing_24h", 0)
    two_way = min(incoming, outgoing) / max(incoming, outgoing, 1)
    activity = min(min(incoming, outgoing) / 100, 1)
    precise = float(account.get("exact_amount_ratio", 0))
    correlated = release_delay_seconds is not None and 0 <= release_delay_seconds <= 120
    score = round(35 * activity * two_way + 30 * precise + 35 * int(correlated), 1)
    return {"score": score, "classification": "P2P_MERCHANT_CANDIDATE" if score >= 65 else "INSUFFICIENT_EVIDENCE",
            "incoming_transactions_24h": incoming, "outgoing_transactions_24h": outgoing,
            "two_way_ratio": round(two_way, 3), "exact_amount_ratio": precise,
            "unique_counterparties": account.get("unique_counterparties", 0),
            "escrow_time_correlated": correlated, "release_delay_seconds": release_delay_seconds,
            "note": "Indikator demo; transaksi dua arah/nominal presisi tidak membuktikan merchant terlibat penipuan."}


class BankTrailer:
    def trace(self, report, scenario: dict) -> tuple[list[TrailNode], list[TrailEdge], list[dict]]:
        accounts = {a["id"]: a for a in scenario["accounts"]}
        tainted = {"victim": report.nominal_transaksi}
        received = dict(tainted)
        hops = {"victim": 0}
        nodes = {}
        edges = []
        bridges = []
        for tx in sorted(scenario["bank_ledger"], key=lambda x: datetime.fromisoformat(x["timestamp"])):
            source, target = tx["source"], tx["target"]
            if datetime.fromisoformat(tx["timestamp"]) < report.waktu_transaksi or source not in tainted:
                continue
            if hops[source] >= 4 or len(edges) >= 50:
                continue
            amount = min(tainted[source], D(tx["amount_idr"]))
            if amount <= 0:
                continue
            tainted[source] -= amount
            tainted[target] = tainted.get(target, D(0)) + amount
            received[target] = received.get(target, D(0)) + amount
            hops[target] = min(hops.get(target, 99), hops[source] + 1)
            for account_id in (source, target):
                a = accounts[account_id]
                is_va = a["kind"] == "PAKD_VA"
                metadata = {**a, "evidence_source": "SYNTHETIC_BANK_LEDGER"}
                if a["kind"] == "P2P_MERCHANT":
                    metadata["heuristic"] = merchant_heuristic(a)
                nodes.setdefault(account_id, TrailNode(id=account_id, label=a["label"],
                    node_zone="CRYPTO_EXCHANGE_BRIDGE" if is_va else "FIAT_BANK", node_type=a["kind"],
                    entity_label=a.get("platform", a["bank"]), account=a["account"], hop=hops[account_id], metadata=metadata))
                nodes[account_id].amount_idr = received[account_id]
            edges.append(TrailEdge(id=tx["id"], source=source, target=target,
                edge_type="VA_DEPOSIT" if accounts[target]["kind"] == "PAKD_VA" else "BANK_TRANSFER",
                label=f"Rp {amount:,.0f}", amount_idr=amount, timestamp=tx["timestamp"],
                metadata={"evidence_source": "SYNTHETIC_BANK_LEDGER", "attribution_model": "chronological_capped_flow"}))
            if accounts[target]["kind"] in {"PAKD_VA", "P2P_MERCHANT"}:
                bridges.append({**tx, "traced_amount_idr": amount, "account": accounts[target]})
        for node in nodes.values():
            node.metadata["remaining_attributed_idr"] = str(tainted.get(node.id, D(0)))
            node.metadata["traced_throughput_idr"] = str(received[node.id])
        return list(nodes.values()), edges, bridges
