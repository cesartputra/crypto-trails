"""IASC recommendation dispatch and decisions owned by the receiving institution."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4
from schemas import InstitutionFreezeDecision
from scenarios import RATE, get_scenario
from services.pakd_api_consumer import SOURCE, PAKD_CODES

D = Decimal
FINAL_STATUSES = {"APPROVED_FREEZE_BY_INSTITUTION", "PARTIAL_FREEZE_BY_INSTITUTION", "REJECTED_BY_INSTITUTION"}


class GovernanceConflict(ValueError):
    pass


def _pakd_identity(node):
    if (node.node_type not in {"PAKD_USER_ACCOUNT", "PAKD_RECEIVER_ACCOUNT"}
        or node.node_zone != "CRYPTO_EXCHANGE_BRIDGE" or node.metadata.get("evidence_source") != SOURCE):
        return None
    uid, provider = node.metadata.get("uid"), node.entity_label
    proofs = [node.metadata[key] for key in ("mirror_deposit", "mirror_match", "mirror_wallet") if node.metadata.get(key)]
    if not uid or not proofs or node.account not in {None, uid}:
        return None
    for proof in proofs:
        raw = proof.get("scoped_wallet") or proof.get("scoped_inflow") or {}
        if (proof.get("user_uid") != uid or proof.get("pakd_name") != provider
            or proof.get("evidence_source") != SOURCE or proof.get("simulation") is not True
            or raw.get("user_uid") != uid or PAKD_CODES.get(raw.get("pakd_code")) != provider
            or proof.get("kyc_nik_masked") != raw.get("kyc_nik_masked")):
            return None
        if "scoped_wallet" in proof and (proof.get("wallet_type") != "CUSTODIAL_DEPOSIT_ADDRESS"
            or raw.get("wallet_role") != "CUSTODIAL_DEPOSIT_ADDRESS"
            or proof.get("wallet_address") != raw.get("wallet_address") or proof.get("chain") != raw.get("chain")):
            return None
    return uid, provider


def _pakd_inventory(node, nodes):
    """Each deposit stock and held order is counted once per provider + UID."""
    metadata = node.metadata
    proof = metadata.get("mirror_match") or metadata.get("mirror_wallet")
    inventory = {}
    if proof:
        address = metadata.get("linked_deposit_address") or metadata.get("deposit_address")
        if address != proof["wallet_address"]:
            return inventory
        crypto = min(D(metadata.get("tainted_balance_crypto", "0")), node.balance_crypto)
        available = D(metadata["attributed_recoverable_amount_idr"]) if "attributed_recoverable_amount_idr" in metadata else crypto * RATE
        if crypto == 0 and available != 0:
            raise ValueError("Nilai recovery tidak didukung saldo akun PAKD")
        inventory[("DEPOSIT", proof["chain"], address)] = (available, crypto, None)
    deposit = metadata.get("mirror_deposit")
    if deposit and not metadata.get("same_uid_loopback"):
        held = {}
        for escrow in nodes:
            evidence = escrow.metadata.get("p2p_evidence", {})
            if (escrow.node_type == "P2P_ESCROW" and escrow.entity_label == node.entity_label
                and escrow.metadata.get("uid") == metadata["uid"] and evidence.get("mirror_deposit") == deposit):
                amount = D(escrow.metadata.get("held_escrow_idr", "0"))
                crypto = D(evidence.get("held_crypto_usdt", "0"))
                if amount != crypto * RATE or amount > D(deposit["remaining_pakd_balance_idr"]):
                    raise ValueError("Nilai recovery held order tidak sesuai ledger scoped PAKD")
                if amount:
                    held[("ORDER", escrow.metadata["order_id"])] = (amount, crypto, escrow.metadata["order_id"])
        if sum((value[0] for value in held.values()), D(0)) != D(deposit["remaining_pakd_balance_idr"]):
            raise ValueError("Saldo recovery PAKD tidak memiliki bukti held order yang sesuai")
        inventory.update(held)
    return inventory


def plan_governance_requests(scenario, nodes):
    accounts = {}
    for node in nodes:
        if scenario.get("same_uid_loopback") and node.id != "scammer-uid":
            continue
        category, uid, inventory = None, None, {}
        if (node.node_type in {"MULE", "FINAL_CASHOUT"} and node.node_zone == "FIAT_BANK"
            and node.account and node.metadata.get("account") == node.account
            and node.metadata.get("bank") == node.entity_label
            and node.metadata.get("kind") == node.node_type
            and (node.metadata.get("imported_read_only") is True or node.metadata.get("evidence_source") == "SYNTHETIC_BANK_SETTLEMENT")):
            category, institution = "PUJK", node.entity_label
            available = D(0) if node.node_type == "MULE" else min(D(node.metadata["available_balance_idr"]), D(node.metadata["attributed_available_balance_idr"]))
            inventory[("BANK", node.account)] = (available, D(0), None)
        elif identity := _pakd_identity(node):
            uid, institution = identity
            category = "PAKD"
            inventory = _pakd_inventory(node, nodes)
        if category is None:
            continue
        key = category, institution, uid or node.account
        available = sum((item[0] for item in inventory.values()), D(0))
        if key not in accounts:
            accounts[key] = {"node": node, "inventory": {}}
        account = accounts[key]
        if available > 0 and not any(item[0] > 0 for item in account["inventory"].values()):
            account["node"] = node
        for stock, value in inventory.items():
            if stock in account["inventory"] and account["inventory"][stock] != value:
                raise ValueError("Saldo recovery duplikat tidak konsisten")
            account["inventory"][stock] = value
    requests = []
    for (category, institution, uid_or_account), account in accounts.items():
        node, inventory = account["node"], account["inventory"]
        available = sum((item[0] for item in inventory.values()), D(0))
        crypto = sum((item[1] for item in inventory.values()), D(0))
        orders = sorted({item[2] for item in inventory.values() if item[2]})
        scope = ("TARGETED_PAKD_ACCOUNT_HOLD" if orders and all(key[0] == "ORDER" for key in inventory)
                 else "UNIFIED_PAKD_ACCOUNT" if category == "PAKD" and (scenario.get("same_uid_loopback") or orders or len(inventory) > 1)
                 else "ACCOUNT_BALANCE")
        unit = f"Tim Anti-Fraud PUJK {institution}" if category == "PUJK" else f"Compliance Officer PAKD {institution}"
        requests.append(InstitutionFreezeDecision(request_id=f"gov-{node.id}-{uuid4()}", target_node_id=node.id,
            institution_category=category, institution_name=institution, requested_amount_idr=available,
            metadata={"request_scope": scope, "target_account_kind": "PAKD_UID_ACCOUNT" if category == "PAKD" else "BANK_ACCOUNT",
                "target_uid": uid_or_account if category == "PAKD" else None,
                "available_amount_idr": str(available), "potential_recoverable_amount_idr": str(available),
                "asset": node.asset, "crypto_amount": str(crypto), "decision_unit": unit,
                "merchant_full_account_freeze": False, "order_id": orders[0] if len(orders) == 1 else None, "order_ids": orders,
                "evidence_preservation_only": available == 0, "simulated_institution_callback_only": True,
                "note": "Keputusan penundaan/freeze akun berada pada institusi penerima; IASC hanya mengirim rekomendasi dan bukti."}))
    limit = D(scenario["handoff"]["fiat_transfer_amount_idr"])
    if any(not D(0) <= request.requested_amount_idr <= limit for request in requests) or sum((request.requested_amount_idr for request in requests), D(0)) > limit:
        raise ValueError("Nilai recovery teratribusi tidak konsisten dengan nominal perkara")
    return requests


def _packet_target(node, request):
    return {"node_id": node.id, "account": request.metadata["target_uid"] or node.account,
        "uid": request.metadata["target_uid"], "account_kind": request.metadata["target_account_kind"],
        "scope": request.metadata["request_scope"], "institution_category": request.institution_category,
        "institution_name": request.institution_name}


def validate_request(graph, request, packet=None):
    if not graph.simulation or graph.fna_handoff is None:
        raise ValueError("Hasil live read-only tanpa handoff FNA terverifikasi tidak dapat menjadi tiket freeze otomatis")
    try:
        scenario = get_scenario(graph.scenario_id)
    except KeyError as exc:
        raise ValueError("Freeze akun memerlukan skenario dan handoff terverifikasi") from exc
    if request.institution_category == "PAKD":
        for account_node in graph.nodes:
            if _pakd_identity(account_node) != (request.metadata.get("target_uid"), request.institution_name):
                continue
            for key in ("mirror_deposit", "mirror_match", "mirror_wallet"):
                proof = account_node.metadata.get(key)
                if not proof:
                    continue
                table = "wallet" if "scoped_wallet" in proof else "inflow"
                raw = proof.get("scoped_" + table)
                if not any(log.response_status_code == 200 and log.response_payload.get(table) == raw
                           and log.response_payload.get("evidence_source") == SOURCE for log in graph.pakd_api_exchange_logs):
                    raise ValueError("Identitas akun PAKD tidak sesuai bukti respons API scoped tersimpan")
    planned = plan_governance_requests(scenario, graph.nodes)
    expected = next((item for item in planned if item.target_node_id == request.target_node_id), None)
    if (not any(item is request for item in graph.governance_requests) or expected is None
        or any(getattr(request, key) != getattr(expected, key) for key in ("institution_category", "institution_name", "requested_amount_idr", "metadata"))):
        raise ValueError("Target freeze wajib rekening PUJK atau akun UID PAKD dengan identitas dan saldo scoped yang sesuai")
    account_key = request.institution_category, request.institution_name, request.metadata["target_uid"] or request.target_node_id
    matching = [item for item in graph.governance_requests if
        (item.institution_category, item.institution_name, item.metadata.get("target_uid") or item.target_node_id) == account_key]
    if len(matching) != 1:
        raise ValueError("Satu akun hanya boleh memiliki satu rekomendasi freeze")
    node = next(n for n in graph.nodes if n.id == request.target_node_id)
    if packet is not None and (packet.get("target") != _packet_target(node, request)
        or packet.get("requested_amount_idr") != str(request.requested_amount_idr)
        or packet.get("request_scope") != request.metadata["request_scope"]):
        raise ValueError("Target akun berubah dari paket bukti yang telah dikirim")
    return node


def build_evidence_packet(graph, request):
    node = validate_request(graph, request)
    evidence = {"iasc_ticket_id": graph.iasc_ticket_id, "fna_trail_id": graph.fna_trail_id, "trace_id": graph.trace_id,
        "simulation": graph.simulation, "mirror_access": "SCOPED_PAKD_REST_API_DEMO",
        "pakd_api_exchange_logs": [log.model_dump(mode="json") for log in graph.pakd_api_exchange_logs],
        "fna_handoff": graph.fna_handoff.model_dump(mode="json"),
        "nodes": [n.model_dump(mode="json") for n in graph.nodes], "edges": [e.model_dump(mode="json") for e in graph.edges],
        "pakd_trails": [r.model_dump(mode="json") for r in graph.pakd_trails],
        "chain_dispersals": [item.model_dump(mode="json") for item in graph.chain_dispersals],
        "p2p_detections": [p.model_dump(mode="json") for p in graph.p2p_detections],
        "request": request.model_dump(mode="json")}
    digest = sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    packet = {"classification": "DRAFT_SIMULATION_ONLY", "request_id": request.request_id,
        "iasc_ticket_id": graph.iasc_ticket_id, "fna_trail_id": graph.fna_trail_id, "trace_id": graph.trace_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "evidence_sha256": digest, "evidence": evidence,
        "target": _packet_target(node, request),
        "requested_amount_idr": str(request.requested_amount_idr), "request_scope": request.metadata["request_scope"],
        "dispatch_channel": "SIMULATED_IASC_OUTBOX", "transmitted": False, "signed": False,
        "authority": "IASC mengirim rekomendasi; keputusan dan pelaksanaan hanya oleh PUJK/PAKD terkait."}
    if request.metadata["order_ids"]:
        packet["targeted_hold"] = {"order_id": request.metadata.get("order_id"), "order_ids": request.metadata["order_ids"],
            "maximum_hold_idr": str(request.requested_amount_idr), "merchant_full_account_freeze": False,
            "preserve_order_kyc_and_settlement_evidence": True}
    return packet


def sync_node_decision(graph, request):
    node = next(n for n in graph.nodes if n.id == request.target_node_id)
    node.institution_decision = request.model_copy(deep=True)
    node.status = request.request_status
    graph.confirmed_frozen_amount_idr = sum((r.approved_frozen_amount_idr for r in graph.governance_requests), D(0))


def send_request(graph, request, packet=None):
    validate_request(graph, request, packet)
    if request.request_status == "NOT_SENT":
        request.request_status = "SENT_WAITING_DECISION"
        request.institution_decision_notes = "Tiket rekomendasi tercatat pada outbox simulasi IASC; menunggu keputusan institusi penerima."
        sync_node_decision(graph, request)
    return request


def decide_request(graph, request, decision_input, packet=None):
    validate_request(graph, request, packet)
    if request.request_status == "NOT_SENT":
        raise GovernanceConflict("Rekomendasi harus dikirim sebelum keputusan institusi dapat disimulasikan")
    requested = request.requested_amount_idr
    available = D(request.metadata["available_amount_idr"])
    status = decision_input.decision
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
