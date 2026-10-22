"""PAKD Scoped Mirror API: REST forward resolution, ledger checks and reverse loopback."""
from datetime import datetime
from decimal import Decimal
from schemas import P2PEscrowMatchDetail, PAKDInternalTrailRecord, TrailNode, TrailEdge
from services.bank_trailer import merchant_heuristic
from scenarios import RATE
from services.pakd_api_consumer import PAKDAPIConsumer, SOURCE
from services.chains import normalize_chain

D = Decimal
PAKD_ZONE = "Zona 2: PAKD Scoped Mirror REST API · simulasi"


class P2PAndPAKDBridge:
    def __init__(self, consumer=None):
        self.consumer = consumer or PAKDAPIConsumer()

    def match_handoff(self, handoff, scenario):
        matches = []
        for intake in scenario["pakd_intake_ledger"]:
            intake_time = datetime.fromisoformat(intake["timestamp"])
            if intake_time.tzinfo is None or intake_time.utcoffset() is None:
                raise ValueError("Waktu intake PAKD wajib menyertakan zona waktu")
            delay = (intake_time - handoff.fiat_transfer_timestamp).total_seconds()
            if (intake["rrn_reference_number"] == handoff.rrn_reference_number
                and intake["gateway_account"] == handoff.pakd_gateway_va_or_account
                and D(intake["fiat_amount_idr"]) == handoff.fiat_transfer_amount_idr and 0 <= delay <= 120):
                matches.append(intake)
        if len(matches) != 1:
            raise ValueError("Handoff FNA tidak memiliki satu kecocokan unik RRN, gateway, nominal dan waktu di ledger PAKD")
        intake = matches[0]
        if intake["pakd_user_uid"] != scenario["onramp"]["uid"] or intake["pakd_name"] != scenario["onramp"]["platform"]:
            raise ValueError("Mapping UID/platform internal PAKD tidak konsisten")
        return intake

    async def resolve_onramp(self, handoff, scenario, nodes, edges):
        intake = self.match_handoff(handoff, scenario)
        ramp = scenario["onramp"]
        match = await self.consumer.consume_pakd_inflow_lookup_api(handoff.pakd_gateway_va_or_account,
            handoff.rrn_reference_number, handoff.fiat_transfer_amount_idr, pakd_code=ramp["platform"])
        initial = next(w for w in scenario["wallets"] if w["id"] == ramp["initial_wallet"])
        normalize_address = str if normalize_chain(ramp["chain"]) == "TRON" else str.lower
        if (not match or match["user_uid"] != ramp["uid"] or match["withdrawal_tx_hash"] != ramp["withdrawal_tx_hash"]
            or normalize_address(match["initial_wallet_address"]) != normalize_address(initial["address"])
            or normalize_chain(match["chain"]) != normalize_chain(ramp["chain"])):
            raise ValueError("Respons scoped API PAKD tidak sesuai UID/withdrawal handoff")
        intake = {**intake, "mirror_deposit": match}
        gateway = next((n for n in nodes if n.account == handoff.pakd_gateway_va_or_account), None)
        if gateway is None:
            raise ValueError("Gateway handoff tidak ada dalam snapshot FNA")
        initial = next(w for w in scenario["wallets"] if w["id"] == ramp["initial_wallet"])
        base_hop = gateway.hop
        evidence = {"rrn_reference_number": handoff.rrn_reference_number, "rrn_exact_match": True,
            "gateway_exact_match": True, "amount_exact_match": True, "bank_transfer_timestamp": handoff.fiat_transfer_timestamp.isoformat(),
            "pakd_intake_timestamp": intake["timestamp"], "intake_id": intake["intake_id"], "fna_trail_id": handoff.fna_trail_id,
            "evidence_source": SOURCE, "mirror_deposit": intake["mirror_deposit"],
            "api_endpoint": intake["mirror_deposit"]["api_endpoint"]}
        nodes.append(TrailNode(id="pakd-fiat-deposit", label=f"PAKD fiat ledger · {ramp['platform']}",
            node_zone="CRYPTO_EXCHANGE_BRIDGE", node_type="PAKD_FIAT_DEPOSIT", entity_label=ramp["platform"],
            amount_idr=handoff.fiat_transfer_amount_idr, hop=base_hop + 1,
            metadata={**evidence, "architecture_zone": PAKD_ZONE, "uid": ramp["uid"]}))
        edges.append(TrailEdge(id="pakd-intake-match", source=gateway.id, target="pakd-fiat-deposit", edge_type="FNA_PAKD_HANDOFF_MATCH",
            label="RRN + gateway + nominal + waktu cocok", amount_idr=handoff.fiat_transfer_amount_idr,
            timestamp=intake["timestamp"], metadata={**evidence, "correlation_only": True}))
        source_id = "pakd-fiat-deposit"
        hop = base_hop + 2
        detections = []
        purchased = D(ramp["crypto_amount"])
        credit_time = datetime.fromisoformat(intake["timestamp"])
        if ramp["kind"] == "P2P":
            accounts = {a["id"]: a for a in scenario["accounts"]}
            matches = []
            for order in scenario["p2p_orders"]:
                delay = (datetime.fromisoformat(order["escrow_release_timestamp"]) - handoff.fiat_transfer_timestamp).total_seconds()
                if (order["p2p_direction"] == "ON_RAMP_DEPOSIT" and order["rrn_reference_number"] == handoff.rrn_reference_number
                    and order["merchant_bank_account"] == handoff.pakd_gateway_va_or_account
                    and D(order["fiat_amount_idr"]) == handoff.fiat_transfer_amount_idr and 0 <= delay <= 120):
                    matches.append((order, delay))
            if len(matches) != 1:
                raise ValueError("Order escrow ambigu atau tidak sesuai RRN, rekening, nominal dan waktu")
            order, delay = matches[0]
            if datetime.fromisoformat(order["escrow_release_timestamp"]) < datetime.fromisoformat(intake["timestamp"]):
                raise ValueError("Pelepasan escrow mendahului penerimaan fiat pada ledger PAKD")
            if order["scammer_uid"] != ramp["uid"] or order["linked_onchain_address"] != initial["address"]:
                raise ValueError("Order escrow tidak sesuai UID atau initial wallet PAKD")
            purchased = D(order["crypto_amount_usdt"])
            held = D(order.get("held_crypto_usdt", "0"))
            if purchased != D(ramp["crypto_amount"]) + held:
                raise ValueError("Jumlah order harus sama dengan kripto ditarik dan sisa escrow")
            evidence.update({"escrow_release_timestamp": order["escrow_release_timestamp"], "release_delay_seconds": delay,
                "account_exact_match": True, "held_crypto_usdt": str(held), "released_crypto_usdt": ramp["crypto_amount"],
                "heuristic": merchant_heuristic(accounts[ramp["bank_target"]], delay), "order_id": order["order_id"]})
            detections.append(P2PEscrowMatchDetail(**{k: v for k, v in order.items() if k in P2PEscrowMatchDetail.model_fields}, evidence=evidence))
            gateway.metadata.update({"heuristic": evidence["heuristic"], "p2p_order_id": order["order_id"], "p2p_evidence": evidence})
            nodes.append(TrailNode(id="escrow", label=f"PAKD P2P escrow · {order['order_id']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="P2P_ESCROW", entity_label=ramp["platform"], amount_idr=handoff.fiat_transfer_amount_idr,
                crypto_amount=purchased, balance_crypto=held, asset=ramp["asset"], hop=hop,
                metadata={"architecture_zone": PAKD_ZONE, "order_id": order["order_id"], "merchant_id": order["merchant_id"],
                    "p2p_evidence": evidence, "held_escrow_idr": ramp.get("held_escrow_idr", "0"), "uid": ramp["uid"]}))
            edges.append(TrailEdge(id="escrow-match", source=source_id, target="escrow", edge_type="ESCROW_RELEASE",
                label="PAKD internal P2P escrow", crypto_amount=purchased, asset=ramp["asset"],
                timestamp=order["escrow_release_timestamp"], metadata={**evidence, "correlation_only": True}))
            source_id, hop, credit_time = "escrow", hop + 1, datetime.fromisoformat(order["escrow_release_timestamp"])
        elif D(ramp["fiat_amount_idr"]) != handoff.fiat_transfer_amount_idr:
            raise ValueError("Mutasi VA tidak sesuai ledger internal PAKD")
        if credit_time > datetime.fromisoformat(ramp["withdrawal_timestamp"]):
            raise ValueError("Withdrawal mendahului kredit kripto internal PAKD")
        nodes.append(TrailNode(id="pakd-conversion", label=f"Konversi / pembelian {purchased} {ramp['asset']}",
            node_zone="CRYPTO_EXCHANGE_BRIDGE", node_type="PAKD_CRYPTO_CONVERSION", entity_label=ramp["platform"],
            crypto_amount=purchased, amount_idr=purchased * RATE, asset=ramp["asset"], hop=hop,
            metadata={"architecture_zone": PAKD_ZONE, "conversion_rate_idr": str(RATE), "fees_idr": scenario.get("fees_idr", "0"),
                "uid": ramp["uid"], "rrn_reference_number": handoff.rrn_reference_number}))
        edges.append(TrailEdge(id="pakd-conversion-credit", source=source_id, target="pakd-conversion", edge_type="PAKD_INTERNAL_CONVERSION",
            label=f"Internal PAKD: {purchased} {ramp['asset']}", crypto_amount=purchased, asset=ramp["asset"],
            timestamp=credit_time, metadata={"offchain": True, "fees_idr": scenario.get("fees_idr", "0")}))
        record = PAKDInternalTrailRecord(record_id=f"PAKD-TRAIL-{intake['intake_id']}", target_node_id="scammer-uid",
            pakd_name=ramp["platform"], intake_mechanism=intake["intake_mechanism"], pakd_user_uid=ramp["uid"],
            kyc_nik_masked=ramp["kyc"]["nik_masked"], fiat_in_idr=handoff.fiat_transfer_amount_idr,
            crypto_purchased_token=ramp["asset"], crypto_purchased_amount=purchased,
            remaining_pakd_balance_idr=D(ramp.get("held_escrow_idr", "0")), withdrawal_status="WITHDRAWN_ONCHAIN",
            withdrawal_tx_hash=ramp["withdrawal_tx_hash"], blockchain_network=ramp["chain"], initial_wallet_address=initial["address"],
            metadata={**evidence, "withdrawal_timestamp": ramp["withdrawal_timestamp"], "withdrawn_crypto_amount": ramp["crypto_amount"],
                "partial_withdrawal": bool(D(ramp.get("held_escrow_idr", "0"))), "fees_idr": scenario.get("fees_idr", "0")})
        nodes.append(TrailNode(id="scammer-uid", label=f"Akun PAKD · {ramp['uid']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
            node_type="PAKD_USER_ACCOUNT", entity_label=ramp["platform"], crypto_amount=D(ramp["crypto_amount"]),
            amount_idr=D(ramp["crypto_amount"]) * RATE, asset=ramp["asset"], hop=hop + 1,
            metadata={"architecture_zone": PAKD_ZONE, "uid": ramp["uid"], "kyc": {"name": match["kyc_name_masked"], "nik_masked": match["kyc_nik_masked"]},
                "pakd_internal_record_id": record.record_id, "evidence_source": SOURCE, "mirror_deposit": intake["mirror_deposit"],
                "api_endpoint": intake["mirror_deposit"]["api_endpoint"]}))
        edges.append(TrailEdge(id="bridge-credit", source="pakd-conversion", target="scammer-uid", edge_type="PAKD_INTERNAL_CREDIT",
            label=f"{ramp['crypto_amount']} {ramp['asset']} → withdrawal", crypto_amount=D(ramp["crypto_amount"]), asset=ramp["asset"],
            timestamp=credit_time, metadata={"offchain": True, "uid": ramp["uid"]}))
        return detections, [record]

    async def resolve_offramp(self, scenario, nodes, edges):
        detections = []
        records = []
        accounts = {a["id"]: a for a in scenario["accounts"]}
        node_ids = {n.id for n in nodes}
        for deposit_node in list(nodes):
            if deposit_node.node_type not in {"DIRECT_EXCHANGE_DEPOSIT", "P2P_ESCROW_DEPOSIT"}:
                continue
            match = await self.consumer.consume_pakd_wallet_reverse_lookup_api(deposit_node.address, deposit_node.chain)
            if not match or match["wallet_type"] != "CUSTODIAL_DEPOSIT_ADDRESS":
                raise ValueError("Deposit wallet tidak memiliki pemetaan custodial pada Scoped Mirror API")
            if match["user_uid"] != deposit_node.metadata.get("uid") or match["pakd_name"] != deposit_node.metadata.get("platform"):
                raise ValueError("Reverse loopback Scoped Mirror API tidak sesuai UID/platform")
            deposit_node.metadata["mirror_match"] = match
            metadata = {**deposit_node.metadata, "mirror_match": match, "kyc_nik_masked": match["nik_masked"]}
            institution = match["pakd_name"]
            account_node_id = f"pakd-account-{deposit_node.id}"
            nodes.append(TrailNode(id=account_node_id, label=f"Akun PAKD penerima · {institution}",
                node_zone="CRYPTO_EXCHANGE_BRIDGE", node_type="PAKD_RECEIVER_ACCOUNT", entity_label=institution,
                amount_idr=deposit_node.balance_crypto * RATE, crypto_amount=deposit_node.crypto_amount,
                balance_crypto=deposit_node.balance_crypto, asset=deposit_node.asset, hop=deposit_node.hop + 1,
                metadata={**metadata, "architecture_zone": PAKD_ZONE, "recovery_institution": "PAKD",
                    "linked_deposit_address": deposit_node.address, "kyc_nik_masked": metadata.get("kyc_nik_masked", scenario["onramp"]["kyc"]["nik_masked"]),
                    "evidence_source": SOURCE}))
            if deposit_node.node_type == "DIRECT_EXCHANGE_DEPOSIT":
                records.append(PAKDInternalTrailRecord(record_id=f"PAKD-DEPOSIT-{metadata.get('uid', deposit_node.id)}", target_node_id=account_node_id,
                    pakd_name=institution, intake_mechanism="ONCHAIN_DEPOSIT", pakd_user_uid=metadata["uid"],
                    kyc_nik_masked=metadata.get("kyc_nik_masked", scenario["onramp"]["kyc"]["nik_masked"]), fiat_in_idr=D(0),
                    crypto_purchased_token=deposit_node.asset, crypto_purchased_amount=deposit_node.crypto_amount,
                    remaining_pakd_balance_idr=deposit_node.balance_crypto * RATE, withdrawal_status="STILL_IN_PAKD_WALLET",
                    blockchain_network=deposit_node.chain,
                    metadata={"deposit_address": deposit_node.address, "evidence_source": SOURCE}))
            deposit_node.metadata.pop("recovery_institution", None)
            deposit_node.metadata["linked_pakd_account_node_id"] = account_node_id
            incoming = [edge for edge in edges if edge.target == deposit_node.id and edge.timestamp]
            edges.append(TrailEdge(id=f"pakd-deposit-credit-{deposit_node.id}", source=deposit_node.id, target=account_node_id,
                edge_type="PAKD_ONCHAIN_DEPOSIT_CREDIT", label=f"Setoran → UID {metadata.get('uid', 'demo')}",
                crypto_amount=deposit_node.crypto_amount, asset=deposit_node.asset,
                timestamp=max((edge.timestamp for edge in incoming), default=None),
                metadata={"correlation_only": True, "evidence_source": SOURCE}))
        sell_orders = [o for o in scenario["p2p_orders"] if o["p2p_direction"] == "OFF_RAMP_WITHDRAWAL"]
        mappings = [(o.get("exchange_node"), o.get("scammer_uid"), o.get("linked_final_bank_account")) for o in sell_orders]
        if len(mappings) != len(set(mappings)) or len({o["order_id"] for o in sell_orders}) != len(sell_orders):
            raise ValueError("Pencocokan sell order ambigu/duplikat")
        spent_per_deposit = {}
        for order in sell_orders:
            if order["p2p_direction"] != "OFF_RAMP_WITHDRAWAL" or order.get("exchange_node") not in node_ids:
                continue
            deposit = next(n for n in nodes if n.id == order["exchange_node"])
            if deposit.address != order["linked_onchain_address"] or deposit.metadata.get("uid") != order["scammer_uid"]:
                continue
            metadata = deposit.metadata
            p2p_match = await self.consumer.consume_pakd_p2p_lookup_api(deposit.address, deposit.chain)
            if not p2p_match or p2p_match["p2p_offramp_account_number"] != order["linked_final_bank_account"]:
                raise ValueError("API P2P PAKD tidak mengonfirmasi rekening settlement scoped")
            target = accounts[order["final_bank_node"]]
            fraction = deposit.taint_score_pct / 100 if deposit.taint_score_pct is not None else D(0)
            if target["account"] != order["linked_final_bank_account"]:
                continue
            credited = sum((e.crypto_amount for e in edges if e.target == deposit.id and e.edge_type == "ONCHAIN_TRANSFER"), D(0))
            spent_per_deposit[deposit.id] = spent_per_deposit.get(deposit.id, D(0)) + D(order["crypto_amount_usdt"])
            deposit_times = [e.timestamp for e in edges if e.target == deposit.id and e.edge_type == "ONCHAIN_TRANSFER" and e.timestamp]
            sell_time = datetime.fromisoformat(order["escrow_release_timestamp"])
            if spent_per_deposit[deposit.id] > credited or not deposit_times or sell_time < max(deposit_times):
                raise ValueError("Sell order melebihi setoran atau terjadi sebelum deposit on-chain")
            settlements = [s for s in scenario.get("bank_settlements", []) if s["order_id"] == order["order_id"]
                and s["source_account"] == order["merchant_bank_account"] and s["target_account"] == target["account"]
                and D(s["amount_idr"]) == D(order["fiat_amount_idr"]) and datetime.fromisoformat(s["timestamp"]) >= sell_time]
            if len(settlements) != 1:
                raise ValueError("Settlement bank tidak sesuai atau ambigu")
            settlement = settlements[0]
            evidence = {"settlement_reference": settlement["reference"], "credited_crypto_usdt": str(credited), "deposit_address_exact_match": True, "exchange_uid_exact_match": True,
                        "settlement_bank_account_exact_match": True, "evidence_source": "SYNTHETIC_EXCHANGE_AND_BANK_SETTLEMENT",
                        "settlement_timestamp": settlement["timestamp"], "bank_transfer_timestamp": settlement["timestamp"],
                        "merchant_payout_bank": "Mandiri", "merchant_payout_account": order["merchant_bank_account"]}
            detections.append(P2PEscrowMatchDetail(**{k: v for k, v in order.items() if k in P2PEscrowMatchDetail.model_fields}, evidence=evidence))
            records.append(PAKDInternalTrailRecord(record_id=f"PAKD-SELL-{order['order_id']}", target_node_id="offramp-settlement",
                pakd_name=order["platform_name"], intake_mechanism="P2P_OFF_RAMP_SETTLEMENT", pakd_user_uid=order["scammer_uid"],
                kyc_nik_masked=metadata.get("kyc_nik_masked", scenario["onramp"]["kyc"]["nik_masked"]), fiat_in_idr=D(0), crypto_purchased_token="USDT",
                crypto_purchased_amount=D(order["crypto_amount_usdt"]), remaining_pakd_balance_idr=deposit.balance_crypto * RATE,
                withdrawal_status="SOLD_VIA_P2P_OFFRAMP", blockchain_network=deposit.chain,
                metadata={**evidence, "fiat_out_idr": order["fiat_amount_idr"], "final_pujk_account": target["account"], "deposit_address": deposit.address,
                          "order_id": order["order_id"], "rrn_reference_number": order["rrn_reference_number"]}))
            deposit.metadata["pakd_internal_record_id"] = records[-1].record_id
            nodes.append(TrailNode(id="offramp-settlement", label=f"P2P sell · {order['order_id']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="P2P_OFFRAMP_SETTLEMENT", entity_label=order["platform_name"], amount_idr=D(order["fiat_amount_idr"]),
                crypto_amount=D(order["crypto_amount_usdt"]), asset="USDT", hop=deposit.hop + 2,
                metadata={"order_id": order["order_id"], "p2p_evidence": evidence, "merchant_id": order["merchant_id"]}))
            nodes.append(TrailNode(id=target["id"], label=target["label"], node_zone="FIAT_BANK", node_type=target["kind"], entity_label=target["bank"],
                account=target["account"], amount_idr=D(order["fiat_amount_idr"]), taint_score_pct=fraction * 100, hop=deposit.hop + 3,
                metadata={**target, "attributed_available_balance_idr": str(min(D(target["available_balance_idr"]), D(order["fiat_amount_idr"])) * fraction),
                          "attributed_settlement_idr": str(D(order["fiat_amount_idr"]) * fraction), "p2p_order_id": order["order_id"], "evidence_source": "SYNTHETIC_BANK_SETTLEMENT"}))
            edges.extend([TrailEdge(id="offramp-order", source=deposit.metadata["linked_pakd_account_node_id"], target="offramp-settlement", edge_type="P2P_SETTLEMENT",
                label=f"Jual {order['crypto_amount_usdt']} USDT", crypto_amount=D(order["crypto_amount_usdt"]), asset="USDT",
                timestamp=order["escrow_release_timestamp"], metadata={"order_id": order["order_id"], "correlation_only": True}),
                TrailEdge(id="offramp-bank", source="offramp-settlement", target=target["id"], edge_type="P2P_SETTLEMENT",
                label=f"Rp {D(order['fiat_amount_idr']):,.0f} cash-out", amount_idr=D(order["fiat_amount_idr"]), timestamp=settlement["timestamp"],
                metadata={"order_id": order["order_id"], "merchant_bank_account": order["merchant_bank_account"]})])
        return detections, records
