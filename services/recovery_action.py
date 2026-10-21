"""Evidence-bound simulated recovery; deliberately no operational bank or chain writes."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from schemas import RecoveryAction
from scenarios import RATE

D = Decimal


def plan_recovery(scenario, nodes):
    actions = []
    ramp = scenario["onramp"]
    for node in nodes:
        if node.node_type == "FINAL_CASHOUT":
            available = min(D(node.metadata.get("attributed_available_balance_idr", node.metadata["available_balance_idr"])), node.amount_idr)
            if available:
                actions.append(RecoveryAction(id="freeze-final-bank", action_type="FREEZE_BANK", node_id=node.id,
                    label="Auto-Freeze rekening BRI akhir · simulasi", amount_idr=available,
                    reason="Settlement P2P terhubung ke rekening penerima; batasi sisa saldo yang teramati.",
                    metadata={"recoverable_contribution_idr": str(available)}))
        kind = node.metadata.get("terminal_recovery")
        if kind and node.balance_crypto > 0:
            exposure = D(node.metadata.get("tainted_balance_crypto", node.balance_crypto))
            if exposure <= 0:
                continue
            actions.append(RecoveryAction(id=f"recovery-{node.id}", action_type=kind, node_id=node.id,
                label=("Freeze akun PAKD" if kind == "FREEZE_PAKD" else "Generate paket Tether/Circle") + " · simulasi",
                amount_idr=exposure * RATE, crypto_amount=exposure, asset=node.asset,
                reason="Sisa saldo terminal teratribusi; tidak menjumlahkan volume transit antar-hop.",
                metadata={"recoverable_contribution_idr": str(exposure * RATE), "address_wide_token_scope": kind == "ISSUER_BLACKLIST_REQUEST"}))
    if ramp["kind"] == "P2P":
        held = D(ramp.get("held_escrow_idr", "0"))
        actions.append(RecoveryAction(id="targeted-p2p-hold", action_type="TARGETED_ESCROW_HOLD", node_id="escrow",
            label="Targeted escrow hold & permintaan bukti · simulasi", amount_idr=held or D(scenario["report"]["nominal_transaksi"]),
            asset="USDT", crypto_amount=held / RATE,
            reason=("Kunci hanya sisa escrow terkait order ini; rekening bisnis merchant tetap aktif." if held
                    else "Escrow telah dilepas; preservasi bukti dan hold terbatas bila dana order masih tersedia, bukan pemulihan retroaktif."),
            metadata={"recoverable_contribution_idr": str(held), "observed_remaining_escrow_idr": str(held),
                      "merchant_full_account_freeze": False, "order_id": next(o["order_id"] for o in scenario["p2p_orders"] if o["p2p_direction"] == "ON_RAMP_DEPOSIT")}))
    return actions


def execute_simulated_action(graph, action):
    node = next(n for n in graph.nodes if n.id == action.node_id)
    evidence = {"report_id": graph.report_id, "trace_id": graph.trace_id,
                "node": node.model_dump(mode="json"), "action": action.model_dump(mode="json"),
                "edges": [e.model_dump(mode="json") for e in graph.edges if e.source == node.id or e.target == node.id],
                "p2p_orders": [p.model_dump(mode="json") for p in graph.p2p_detections]}
    digest = sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    packet = {"classification": "DRAFT_SIMULATION_ONLY", "report_id": graph.report_id, "trace_id": graph.trace_id,
              "created_at": datetime.now(timezone.utc).isoformat(), "action_type": action.action_type,
              "target": {"node_id": node.id, "account": node.account, "address": node.address, "chain": node.chain,
                         "exchange_uid": node.metadata.get("uid")},
              "evidentiary_exposure_idr": str(action.amount_idr), "evidence_sha256": digest,
              "evidence": evidence, "required_authority": "Tinjauan penyidik/otoritas berwenang dan persetujuan mitra; bukan surat resmi OJK.",
              "transmitted": False, "signed": False}
    if action.action_type == "ISSUER_BLACKLIST_REQUEST":
        if node.asset not in {"USDT", "USDC"} or node.chain not in {"TRON_TRC20", "ETHEREUM_ERC20"}:
            raise ValueError("Paket issuer belum didukung untuk token/jaringan ini")
        issuer = "Tether" if node.asset == "USDT" else "Circle"
        function = "addBlackList(address)" if node.asset == "USDT" else "blacklist(address)"
        contract_refs = {("USDT", "ETHEREUM_ERC20"): "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                         ("USDT", "TRON_TRC20"): "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                         ("USDC", "ETHEREUM_ERC20"): "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"}
        if (node.asset, node.chain) not in contract_refs:
            raise ValueError("Token/jaringan belum memiliki referensi kontrak issuer yang didukung")
        packet["issuer_request"] = {"issuer": issuer, "token": node.asset, "network": node.chain,
            "contract_reference": contract_refs.get((node.asset, node.chain)), "function_signature": function,
            "arguments": {"address": node.address}, "expected_event": "AddedBlackList(address)" if node.asset == "USDT" else "Blacklisted(address)",
            "evidentiary_exposure_crypto": str(action.crypto_amount),
            "scope": "ADDRESS_WIDE_TOKEN_RESTRICTION", "note": "Jumlah adalah paparan bukti; blacklist issuer membatasi token pada seluruh address, bukan hanya nominal scam. AddedBlackList adalah event, bukan fungsi.",
            "execution": "ISSUER_PRIVILEGED_ROLE_REQUIRED_NO_TRANSACTION_BROADCAST"}
        status = "DRAFT_GENERATED"
        message = "Paket permohonan issuer tersusun. Belum dikirim, ditandatangani, atau dieksekusi di blockchain."
    elif action.action_type == "TARGETED_ESCROW_HOLD":
        packet["targeted_hold"] = {"order_id": action.metadata["order_id"], "maximum_hold_idr": str(action.amount_idr),
            "observed_remaining_escrow_idr": action.metadata["observed_remaining_escrow_idr"],
            "merchant_full_account_freeze": False, "preserve_order_kyc_and_settlement_evidence": True}
        status = "SIMULATED_TARGETED_HOLD"
        message = "Simulasi hold pada order terkait selesai; rekening merchant tidak diblokir total."
    else:
        packet["freeze_request"] = {"maximum_amount_idr": str(action.amount_idr), "scope": "IDENTIFIED_TERMINAL_ACCOUNT_ONLY"}
        status = "SIMULATED_FROZEN"
        message = "Simulasi pembekuan saldo rekening/akun terminal selesai; tidak ada instruksi dikirim ke mitra."
    node.status = status
    action.status = status
    return {"action_id": action.id, "status": status, "simulation": True, "message": message, "packet": packet, "node_id": node.id}
