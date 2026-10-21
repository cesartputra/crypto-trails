"""Resolve fixture bank postings against explicit VA/escrow records."""
from datetime import datetime
from decimal import Decimal
from schemas import P2PEscrowMatchDetail, TrailNode, TrailEdge
from services.bank_trailer import merchant_heuristic

D = Decimal


class P2PAndPAKDBridge:
    def resolve_onramp(self, scenario, nodes, edges, candidates):
        ramp = scenario["onramp"]
        postings = [p for p in candidates if p["target"] == ramp["bank_target"]]
        if len(postings) != 1:
            raise ValueError("Tidak ada satu mutasi bank unik untuk resolusi bridge")
        posting = postings[0]
        detected = []
        bridge_source = ramp["bank_target"]
        base_hop = next(n.hop for n in nodes if n.id == bridge_source)
        if ramp["kind"] == "P2P":
            matches = []
            for order in scenario["p2p_orders"]:
                delay = (datetime.fromisoformat(order["escrow_release_timestamp"]) - datetime.fromisoformat(posting["timestamp"])).total_seconds()
                if (order["p2p_direction"] == "ON_RAMP_DEPOSIT" and order["merchant_bank_account"] == posting["account"]["account"]
                    and D(order["fiat_amount_idr"]) == D(posting["amount_idr"]) and 0 <= delay <= 120):
                    matches.append((order, delay))
            if len(matches) != 1:
                raise ValueError("Order escrow ambigu atau tidak sesuai rekening, nominal, dan jendela waktu")
            order, delay = matches[0]
            evidence = {"bank_transfer_timestamp": posting["timestamp"], "escrow_release_timestamp": order["escrow_release_timestamp"],
                        "amount_exact_match": True, "account_exact_match": True, "release_delay_seconds": delay,
                        "heuristic": merchant_heuristic(posting["account"], delay),
                        "evidence_source": "SYNTHETIC_EXCHANGE_ORDER", "match_confidence": "EXACT_FIXTURE_MATCH",
                        "held_crypto_usdt": order.get("held_crypto_usdt", "0"),
                        "released_crypto_usdt": order.get("released_crypto_usdt", order["crypto_amount_usdt"])}
            detected.append(P2PEscrowMatchDetail(**{k: v for k, v in order.items() if k in P2PEscrowMatchDetail.model_fields}, evidence=evidence))
            merchant = next(n for n in nodes if n.id == bridge_source)
            merchant.metadata["heuristic"] = evidence["heuristic"]
            merchant.metadata["p2p_order_id"] = order["order_id"]
            merchant.metadata["p2p_evidence"] = evidence
            nodes.append(TrailNode(id="escrow", label=f"P2P escrow · {order['order_id']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="P2P_ESCROW", entity_label=ramp["platform"], amount_idr=D(order["fiat_amount_idr"]),
                crypto_amount=D(order["crypto_amount_usdt"]), balance_crypto=D(order.get("held_crypto_usdt", "0")), asset="USDT", hop=base_hop + 1,
                metadata={"order_id": order["order_id"], "merchant_id": order["merchant_id"], "p2p_evidence": evidence,
                          "held_escrow_idr": ramp.get("held_escrow_idr", "0")}))
            edges.append(TrailEdge(id="escrow-match", source=bridge_source, target="escrow", edge_type="ESCROW_RELEASE",
                label="Bank ↔ escrow · exact match", amount_idr=D(order["fiat_amount_idr"]), timestamp=order["escrow_release_timestamp"],
                metadata={"correlation_only": True, "order_id": order["order_id"], **evidence}))
            bridge_source = "escrow"
            base_hop += 1
        elif D(posting["amount_idr"]) != D(ramp["fiat_amount_idr"]):
            raise ValueError("Mutasi VA tidak sesuai catatan exchange")
        nodes.append(TrailNode(id="scammer-uid", label=f"Akun kripto · {ramp['uid']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
            node_type="SCAMMER_EXCHANGE_ACCOUNT", entity_label=ramp["platform"], crypto_amount=D(ramp["crypto_amount"]),
            amount_idr=D(ramp["crypto_amount"]) * D(16000), asset=ramp["asset"], hop=base_hop + 1,
            metadata={"uid": ramp["uid"], "kyc": ramp["kyc"], "evidence_source": "SYNTHETIC_KYC_EXCHANGE_MAPPING"}))
        edges.append(TrailEdge(id="bridge-credit", source=bridge_source, target="scammer-uid",
            edge_type="ESCROW_RELEASE" if ramp["kind"] == "P2P" else "VA_DEPOSIT", label=f"{ramp['crypto_amount']} {ramp['asset']} credited",
            crypto_amount=D(ramp["crypto_amount"]), asset=ramp["asset"], timestamp=order["escrow_release_timestamp"] if ramp["kind"] == "P2P" else datetime.fromisoformat(posting["timestamp"]),
            metadata={"offchain": True, "uid": ramp["uid"]}))
        return detected

    def resolve_offramp(self, scenario, nodes, edges):
        detections = []
        accounts = {a["id"]: a for a in scenario["accounts"]}
        node_ids = {n.id for n in nodes}
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
            nodes.append(TrailNode(id="offramp-settlement", label=f"P2P sell · {order['order_id']}", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="P2P_OFFRAMP_SETTLEMENT", entity_label=order["platform_name"], amount_idr=D(order["fiat_amount_idr"]),
                crypto_amount=D(order["crypto_amount_usdt"]), asset="USDT", hop=deposit.hop + 1,
                metadata={"order_id": order["order_id"], "p2p_evidence": evidence, "merchant_id": order["merchant_id"]}))
            nodes.append(TrailNode(id=target["id"], label=target["label"], node_zone="FIAT_BANK", node_type=target["kind"], entity_label=target["bank"],
                account=target["account"], amount_idr=D(order["fiat_amount_idr"]), taint_score_pct=fraction * 100, hop=deposit.hop + 2,
                metadata={**target, "attributed_available_balance_idr": str(min(D(target["available_balance_idr"]), D(order["fiat_amount_idr"])) * fraction),
                          "attributed_settlement_idr": str(D(order["fiat_amount_idr"]) * fraction), "p2p_order_id": order["order_id"], "evidence_source": "SYNTHETIC_BANK_SETTLEMENT"}))
            edges.extend([TrailEdge(id="offramp-order", source=deposit.id, target="offramp-settlement", edge_type="P2P_SETTLEMENT",
                label=f"Jual {order['crypto_amount_usdt']} USDT", crypto_amount=D(order["crypto_amount_usdt"]), asset="USDT",
                timestamp=order["escrow_release_timestamp"], metadata={"order_id": order["order_id"], "correlation_only": True}),
                TrailEdge(id="offramp-bank", source="offramp-settlement", target=target["id"], edge_type="P2P_SETTLEMENT",
                label=f"Rp {D(order['fiat_amount_idr']):,.0f} cash-out", amount_idr=D(order["fiat_amount_idr"]), timestamp=order["escrow_release_timestamp"],
                metadata={"order_id": order["order_id"], "merchant_bank_account": order["merchant_bank_account"]})])
        return detections
