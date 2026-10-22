"""IASC recommendation dispatch and decisions owned by the receiving institution."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4
from schemas import InstitutionFreezeDecision
from scenarios import RATE

D = Decimal
FINAL_STATUSES = {"APPROVED_FREEZE_BY_INSTITUTION", "PARTIAL_FREEZE_BY_INSTITUTION", "REJECTED_BY_INSTITUTION"}


class GovernanceConflict(ValueError):
    pass


def plan_governance_requests(scenario, nodes):
    requests = []
    ramp = scenario["onramp"]
    for node in nodes:
        category, institution, scope, available = None, None, "ACCOUNT_BALANCE", D(0)
        crypto = D(0)
        if node.node_type == "MULE":
            category, institution = "PUJK", node.entity_label
        elif node.node_type == "FINAL_CASHOUT":
            category, institution = "PUJK", node.entity_label
            available = min(D(node.metadata["available_balance_idr"]), D(node.metadata["attributed_available_balance_idr"]))
        elif node.node_type == "PAKD_USER_ACCOUNT":
            category, institution = "PAKD", node.entity_label
        elif node.node_type == "P2P_ESCROW":
            category, institution, scope = "PAKD", node.entity_label, "TARGETED_ESCROW"
            available = D(ramp.get("held_escrow_idr", "0"))
            crypto = available / RATE
        elif node.metadata.get("recovery_institution") == "PAKD" and node.balance_crypto > 0:
            category, institution = "PAKD", node.metadata.get("platform", node.entity_label)
            crypto = D(node.metadata.get("tainted_balance_crypto", node.balance_crypto))
            available = crypto * RATE
        elif node.metadata.get("recovery_institution") == "ISSUER" and node.balance_crypto > 0:
            category, institution, scope = "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE", "Tether" if node.asset == "USDT" else "Circle", "ISSUER_REVIEW"
            crypto = D(node.metadata.get("tainted_balance_crypto", node.balance_crypto))
            available = crypto * RATE
        if category is None:
            continue
        unit = (f"Tim Anti-Fraud PUJK {institution}" if category == "PUJK" else
                f"Compliance Officer PAKD {institution}" if category == "PAKD" else f"Compliance Unit {institution}")
        requests.append(InstitutionFreezeDecision(request_id=f"gov-{node.id}-{uuid4()}", target_node_id=node.id,
            institution_category=category, institution_name=institution, requested_amount_idr=available,
            metadata={"request_scope": scope, "available_amount_idr": str(available), "potential_recoverable_amount_idr": str(available),
                "asset": node.asset, "crypto_amount": str(crypto), "decision_unit": unit,
                "merchant_full_account_freeze": False, "order_id": node.metadata.get("order_id"),
                "evidence_preservation_only": available == 0, "simulated_institution_callback_only": True,
                "note": "Keputusan penundaan/freeze berada pada institusi penerima; IASC hanya mengirim rekomendasi dan bukti."}))
    return requests


def build_evidence_packet(graph, request):
    if not graph.simulation or graph.fna_handoff is None:
        raise ValueError("Hasil live read-only tanpa handoff FNA terverifikasi tidak dapat menjadi tiket freeze otomatis")
    node = next(n for n in graph.nodes if n.id == request.target_node_id)
    evidence = {"iasc_ticket_id": graph.iasc_ticket_id, "fna_trail_id": graph.fna_trail_id, "trace_id": graph.trace_id,
        "simulation": graph.simulation, "mirror_access": "SCOPED_PAKD_REST_API_DEMO",
        "pakd_api_exchange_logs": [log.model_dump(mode="json") for log in graph.pakd_api_exchange_logs],
        "fna_handoff": graph.fna_handoff.model_dump(mode="json"),
        "nodes": [n.model_dump(mode="json") for n in graph.nodes], "edges": [e.model_dump(mode="json") for e in graph.edges],
        "pakd_trails": [r.model_dump(mode="json") for r in graph.pakd_trails],
        "p2p_detections": [p.model_dump(mode="json") for p in graph.p2p_detections],
        "request": request.model_dump(mode="json")}
    digest = sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    packet = {"classification": "DRAFT_SIMULATION_ONLY", "request_id": request.request_id,
        "iasc_ticket_id": graph.iasc_ticket_id, "fna_trail_id": graph.fna_trail_id, "trace_id": graph.trace_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "evidence_sha256": digest, "evidence": evidence,
        "target": {"node_id": node.id, "account": node.account, "address": node.address, "chain": node.chain,
                   "institution_category": request.institution_category, "institution_name": request.institution_name},
        "requested_amount_idr": str(request.requested_amount_idr), "request_scope": request.metadata["request_scope"],
        "dispatch_channel": "SIMULATED_IASC_OUTBOX", "transmitted": False, "signed": False,
        "authority": "IASC mengirim rekomendasi; keputusan dan pelaksanaan hanya oleh PUJK/PAKD/issuer terkait."}
    if request.metadata["request_scope"] == "TARGETED_ESCROW":
        packet["targeted_hold"] = {"order_id": request.metadata.get("order_id"),
            "maximum_hold_idr": str(request.requested_amount_idr), "merchant_full_account_freeze": False,
            "preserve_order_kyc_and_settlement_evidence": True}
    if request.metadata["request_scope"] == "ISSUER_REVIEW":
        contract_refs = {("USDT", "ETHEREUM_ERC20"): "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                         ("USDT", "TRON_TRC20"): "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                         ("USDC", "ETHEREUM_ERC20"): "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"}
        if (node.asset, node.chain) not in contract_refs:
            raise ValueError("Token/jaringan tidak memiliki referensi kontrak issuer yang didukung")
        packet["issuer_request"] = {"issuer": "Tether" if node.asset == "USDT" else "Circle", "token": node.asset,
            "network": node.chain, "contract_reference": contract_refs[(node.asset, node.chain)],
            "function_signature": "addBlackList(address)" if node.asset == "USDT" else "blacklist(address)",
            "expected_event": "AddedBlackList(address)" if node.asset == "USDT" else "Blacklisted(address)",
            "arguments": {"address": node.address}, "evidentiary_exposure_crypto": request.metadata["crypto_amount"],
            "scope": "ADDRESS_WIDE_TOKEN_RESTRICTION",
            "execution": "ISSUER_PRIVILEGED_ROLE_REQUIRED_NO_TRANSACTION_BROADCAST",
            "note": "Paket draf untuk review issuer. AddedBlackList adalah event, bukan fungsi. Nominal bukti bukan pembatasan blacklist parsial pada alamat."}
    return packet


def sync_node_decision(graph, request):
    node = next(n for n in graph.nodes if n.id == request.target_node_id)
    node.institution_decision = request.model_copy(deep=True)
    node.status = request.request_status
    graph.confirmed_frozen_amount_idr = sum((r.approved_frozen_amount_idr for r in graph.governance_requests), D(0))


def send_request(graph, request):
    if request.request_status == "NOT_SENT":
        request.request_status = "SENT_WAITING_DECISION"
        request.institution_decision_notes = "Tiket rekomendasi tercatat pada outbox simulasi IASC; menunggu keputusan institusi penerima."
        sync_node_decision(graph, request)
    return request


def decide_request(graph, request, decision_input):
    if request.request_status == "NOT_SENT":
        raise GovernanceConflict("Rekomendasi harus dikirim sebelum keputusan institusi dapat disimulasikan")
    requested = request.requested_amount_idr
    available = D(request.metadata["available_amount_idr"])
    status = decision_input.decision
    if request.metadata["request_scope"] == "ISSUER_REVIEW" and status == "PARTIAL_FREEZE_BY_INSTITUTION":
        raise ValueError("Blacklist issuer membatasi token pada seluruh alamat; partial freeze nominal tidak didukung")
    amount = decision_input.approved_frozen_amount_idr
    if amount is None:
        amount = requested if status == "APPROVED_FREEZE_BY_INSTITUTION" else min(requested, available) / 2 if status == "PARTIAL_FREEZE_BY_INSTITUTION" else D(0)
        amount = amount.quantize(D("0.01"))
    if status == "APPROVED_FREEZE_BY_INSTITUTION":
        if requested <= 0 or amount != requested or amount > available:
            raise ValueError("Persetujuan penuh wajib sama dengan nominal diminta dan tidak melebihi saldo eligible")
        default_notes = "Institusi menyetujui rekomendasi dan melaporkan saldo diamankan dalam callback simulasi."
    elif status == "PARTIAL_FREEZE_BY_INSTITUTION":
        if not D(0) < amount < requested or amount > available:
            raise ValueError("Freeze sebagian harus positif, lebih kecil dari nominal diminta, dan tidak melebihi saldo eligible")
        default_notes = "Institusi melaporkan hanya sebagian saldo dapat diamankan dalam callback simulasi."
    else:
        if amount != 0:
            raise ValueError("Keputusan penolakan tidak boleh mengakui nominal beku")
        default_notes = "Institusi menolak: saldo tidak tersedia pada saat review simulasi; lanjutkan penelusuran atau permintaan bukti."
    notes = decision_input.institution_decision_notes or default_notes
    if request.request_status in FINAL_STATUSES:
        if request.request_status != status or request.approved_frozen_amount_idr != amount or request.institution_decision_notes != notes:
            raise GovernanceConflict("Keputusan final tidak dapat ditimpa; callback ulang harus identik")
        return request
    projected = sum((r.approved_frozen_amount_idr for r in graph.governance_requests if r.request_id != request.request_id), D(0)) + amount
    if projected > graph.recoverable_amount_idr:
        raise ValueError("Total keputusan melampaui saldo eligible yang teratribusi")
    request.request_status = status
    request.approved_frozen_amount_idr = amount
    request.institution_decision_notes = notes
    request.decided_by_unit = request.metadata["decision_unit"]
    request.decision_timestamp = datetime.now(timezone.utc)
    sync_node_decision(graph, request)
    return request
