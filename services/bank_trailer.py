"""Import FNA handoff snapshots; PUJK money-trailing remains an upstream responsibility."""
from datetime import datetime
from decimal import Decimal
from schemas import InstitutionFreezeDecision, TrailEdge, TrailNode

D = Decimal
FNA_ZONE = "Zona 1: FNA’s Money Trails (Jalur PUJK)"


def merchant_heuristic(account, release_delay_seconds=None):
    incoming, outgoing = account.get("incoming_24h", 0), account.get("outgoing_24h", 0)
    two_way = min(incoming, outgoing) / max(incoming, outgoing, 1)
    activity = min(min(incoming, outgoing) / 100, 1)
    precise = float(account.get("exact_amount_ratio", 0))
    correlated = release_delay_seconds is not None and 0 <= release_delay_seconds <= 120
    score = round(35 * activity * two_way + 30 * precise + 35 * int(correlated), 1)
    return {"score": score, "classification": "P2P_MERCHANT_CANDIDATE" if score >= 65 else "INSUFFICIENT_EVIDENCE",
            "incoming_transactions_24h": incoming, "outgoing_transactions_24h": outgoing,
            "two_way_ratio": round(two_way, 3), "exact_amount_ratio": precise,
            "escrow_time_correlated": correlated, "release_delay_seconds": release_delay_seconds,
            "note": "Indikator review PAKD; tidak membuktikan keterlibatan merchant dalam penipuan."}


class FNAHandoffAdapter:
    def import_snapshot(self, handoff, scenario):
        snapshot = scenario["fna_snapshot"]
        if snapshot["fna_trail_id"] != handoff.fna_trail_id:
            raise ValueError("ID trail tidak sesuai snapshot FNA")
        records = snapshot["edges"]
        accounts = {a["id"]: a for a in snapshot["nodes"]}
        if not records or len(records) > 50 or len(accounts) > 64:
            raise ValueError("Snapshot FNA kosong atau melampaui batas")
        if len({e["id"] for e in records}) != len(records):
            raise ValueError("Duplikat referensi pada snapshot FNA")
        if len(accounts) != len(snapshot["nodes"]) or any(record["source"] not in accounts or record["target"] not in accounts for record in records):
            raise ValueError("Referensi rekening snapshot FNA tidak lengkap atau duplikat")
        previous_time = None
        for record in records:
            timestamp = datetime.fromisoformat(record["timestamp"])
            if timestamp.tzinfo is None or timestamp.utcoffset() is None or (previous_time is not None and timestamp < previous_time):
                raise ValueError("Waktu snapshot FNA wajib berzona dan berurutan")
            previous_time = timestamp
        last = records[-1]
        if (accounts[last["source"]]["account"] != handoff.last_fna_account_number
            or accounts[last["source"]]["bank"] != handoff.last_fna_pujk_name
            or accounts[last["target"]]["account"] != handoff.pakd_gateway_va_or_account
            or D(last["amount_idr"]) != handoff.fiat_transfer_amount_idr
            or datetime.fromisoformat(last["timestamp"]) != handoff.fiat_transfer_timestamp):
            raise ValueError("Snapshot FNA tidak sesuai titik handoff")
        edges = []
        for record in records:
            if record["source"] not in accounts or record["target"] not in accounts or D(record["amount_idr"]) <= 0:
                raise ValueError("Referensi rekening atau nominal snapshot FNA tidak valid")
            edges.append(TrailEdge(id=record["id"], source=record["source"], target=record["target"],
                edge_type="FNA_IMPORTED_TRANSFER", label=f"FNA · Rp {D(record['amount_idr']):,.0f}",
                amount_idr=D(record["amount_idr"]), timestamp=record["timestamp"],
                metadata={"evidence_source": snapshot["source"], "fna_trail_id": handoff.fna_trail_id,
                          "imported_read_only": True, "bank_traversal_performed_here": False}))
        nodes = []
        for index, account in enumerate(accounts.values()):
            kind = account["kind"]
            incoming = [e.amount_idr for e in edges if e.target == account["id"]]
            metadata = {**account, "architecture_zone": FNA_ZONE, "evidence_source": snapshot["source"],
                        "fna_trail_id": handoff.fna_trail_id, "imported_read_only": True,
                        "bank_traversal_performed_here": False}
            prior = None
            if kind == "MULE":
                prior = InstitutionFreezeDecision(request_id=f"historical-fna-{account['id']}", target_node_id=account["id"],
                    institution_category="PUJK", institution_name=account["bank"], requested_amount_idr=handoff.fiat_transfer_amount_idr,
                    request_status="REJECTED_BY_INSTITUTION", institution_decision_notes="Saldo tidak tersedia; dana sudah berpindah ke gateway PAKD/P2P. Lanjutkan PAKD Trails.",
                    decided_by_unit=f"Tim Anti-Fraud PUJK {account['bank']}", decision_timestamp=handoff.fiat_transfer_timestamp,
                    metadata={"source": "SYNTHETIC_FNA_PRIOR_PUJK_DECISION", "historical": True})
                metadata["fna_institution_decision"] = prior.model_dump(mode="json")
                metadata["available_balance_idr"] = "0"
            nodes.append(TrailNode(id=account["id"], label=account["label"],
                node_zone="CRYPTO_EXCHANGE_BRIDGE" if kind == "PAKD_VA" else "FIAT_BANK", node_type=kind,
                entity_label=account.get("platform", account["bank"]), account=account["account"],
                amount_idr=sum(incoming, D(0)) if incoming else handoff.fiat_transfer_amount_idr,
                institution_decision=prior, hop=index, metadata=metadata))
        return nodes, edges

    def manual_snapshot(self, handoff):
        metadata = {"evidence_source": "UNVERIFIED_FNA_HANDOFF_INPUT", "fna_trail_id": handoff.fna_trail_id,
                    "architecture_zone": FNA_ZONE, "bank_traversal_performed_here": False}
        nodes = [TrailNode(id="victim", label=f"Korban · {handoff.victim_pujk_name}", node_zone="FIAT_BANK", node_type="VICTIM",
            entity_label=handoff.victim_pujk_name, account=handoff.victim_account_number,
            metadata={**metadata, "owner": handoff.victim_account_name})]
        source_id = "victim"
        edges = []
        if handoff.last_fna_account_number != handoff.victim_account_number or handoff.last_fna_pujk_name != handoff.victim_pujk_name:
            source_id = "last-fna"
            nodes.append(TrailNode(id=source_id, label=f"Titik akhir FNA · {handoff.last_fna_pujk_name}", node_zone="FIAT_BANK",
                node_type="FNA_HANDOFF_ACCOUNT", entity_label=handoff.last_fna_pujk_name, account=handoff.last_fna_account_number,
                hop=1, metadata=metadata))
            edges.append(TrailEdge(id="unverified-context", source="victim", target=source_id, edge_type="FNA_CONTEXT",
                label="Konteks handoff · rute PUJK belum disertakan", metadata={**metadata, "correlation_only": True}))
        nodes.append(TrailNode(id="gateway", label="Gateway PAKD/P2P · belum dicocokkan", node_zone="CRYPTO_EXCHANGE_BRIDGE",
            node_type="UNVERIFIED_GATEWAY", account=handoff.pakd_gateway_va_or_account, hop=2, metadata=metadata))
        edges.append(TrailEdge(id="unverified-handoff", source=source_id, target="gateway", edge_type="FNA_HANDOFF",
            label="Handoff belum diverifikasi", amount_idr=handoff.fiat_transfer_amount_idr, timestamp=handoff.fiat_transfer_timestamp,
            metadata={**metadata, "rrn_reference_number": handoff.rrn_reference_number}))
        return nodes, edges
