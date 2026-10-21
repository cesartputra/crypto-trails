"""Small, auditable synthetic ledgers: no customer or operational OJK data."""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256

D = Decimal
BASE_TIME = datetime.fromisoformat("2026-10-21T09:00:00+07:00")
RATE = D("16000")


def stamp(seconds=0):
    return (BASE_TIME + timedelta(seconds=seconds)).isoformat()


def handoff_seed(scenario_id, bank, amount, victim, mule):
    return {"iasc_ticket_id": f"IASC-DEMO-{scenario_id.upper()}", "fna_trail_id": f"FNA-DEMO-{scenario_id.upper()}",
            "scenario_id": scenario_id, "victim_pujk_name": bank, "victim_account_number": victim,
            "victim_account_name": "Pelapor Demo", "fiat_transfer_amount_idr": str(amount)}


def account(id, label, bank, number, kind="MULE", **metadata):
    return {"id": id, "label": label, "bank": bank, "account": number, "kind": kind, **metadata}


def bank_tx(id, source, target, amount, seconds):
    return {"id": id, "source": source, "target": target, "amount_idr": str(amount), "timestamp": stamp(seconds)}


def wallet(id, address, label, kind="UNHOSTED_WALLET", balance="0", clean_balance="0", **extra):
    return {"id": id, "address": address, "label": label, "kind": kind,
            "balance_crypto": str(balance), "clean_balance_crypto": str(clean_balance), **extra}


def chain_tx(id, source, target, amount, seconds, asset="USDT", chain="TRON_TRC20"):
    return {"id": id, "source": source, "target": target, "crypto_amount": str(amount),
            "timestamp": stamp(seconds), "asset": asset, "chain": chain,
            "tx_hash": ("0x" if chain != "TRON_TRC20" else "") + sha256(id.encode()).hexdigest()}


def p2p_order(id, merchant, merchant_account, fiat, crypto, uid, address, seconds,
              direction="ON_RAMP_DEPOSIT", platform="PAKD Demo P2P", final_account=None, **extra):
    return {"order_id": id, "merchant_id": merchant, "merchant_bank_account": merchant_account,
            "fiat_amount_idr": str(fiat), "crypto_amount_usdt": str(crypto), "scammer_uid": uid,
            "linked_onchain_address": address, "escrow_release_timestamp": stamp(seconds),
            "p2p_direction": direction, "platform_name": platform,
            "linked_final_bank_account": final_account, **extra}


def fixtures():
    initial1 = "TLa2f6D8sR3j7Q9wK5mN4bV2xY6zH8cP1U"
    a1 = [account("victim", "Korban · BNI", "BNI", "012345670001", "VICTIM"),
          account("mule1", "Mule Tier-1 · BCA", "BCA", "0881122334"),
          account("merchant", "Merchant P2P · Mandiri", "Mandiri", "1310099887711", "P2P_MERCHANT",
                  merchant_id="MCH-8821", incoming_24h=128, outgoing_24h=131, exact_amount_ratio="0.97",
                  unique_counterparties=92),
          account("final-bank", "Cash-out akhir · BRI", "BRI", "034101009988111", "FINAL_CASHOUT",
                  owner="Komplotan Akhir", available_balance_idr="144000000")]
    s1 = {"id": "p2p-full-cycle", "title": "FNA → PAKD P2P Full-Cycle → Keputusan PUJK",
          "description": "Deposit melalui merchant P2P, layering TRON, lalu P2P cash-out ke rekening BRI akhir.",
          "handoff": handoff_seed("p2p-full-cycle", "BNI", "180000000", a1[0]["account"], a1[1]["account"]),
          "accounts": a1, "bank_ledger": [bank_tx("b1", "victim", "mule1", "180000000", 0),
                                           bank_tx("b2", "mule1", "merchant", "180000000", 40)],
          "onramp": {"kind": "P2P", "bank_target": "merchant", "uid": "TKR-00421", "platform": "PAKD Demo P2P",
                     "initial_wallet": "w0", "crypto_amount": "11250", "chain": "TRON_TRC20", "asset": "USDT",
                     "kyc": {"name": "Identitas Taker Demo 421", "document_ref": "KYC-SYNTH-421", "status": "SYNTHETIC_VERIFIED"}},
          "p2p_orders": [p2p_order("P2P-BUY-8821", "MCH-8821", a1[2]["account"], "180000000", "11250", "TKR-00421", initial1, 82),
                         p2p_order("P2P-SELL-9104", "MCH-9104", "1390011223300", "160000000", "10000", "TKR-00421",
                                   "TU4v8wJ3cP6sR2mN9xK5zB7hD1qY4fL6aC", 360, "OFF_RAMP_WITHDRAWAL",
                                   final_account=a1[3]["account"], exchange_node="deposit", final_bank_node="final-bank")],
          "wallets": [wallet("w0", initial1, "Initial wallet · TLa2f6…"),
                      wallet("w1", "TX9k3aB6cD8fG2hJ4mN5pQ7rS1uV9wY6zA", "Unhosted · TX9k…"),
                      wallet("w2", "TR8m2pQ5sV7xY1zA3bC6dF9gH4jK8nL2uW", "Unhosted · TR8m…"),
                      wallet("deposit", "TU4v8wJ3cP6sR2mN9xK5zB7hD1qY4fL6aC", "P2P exchange deposit · TU4v…", "P2P_ESCROW_DEPOSIT", balance="1250",
                             platform="PAKD Demo P2P", uid="TKR-00421", recovery_institution="PAKD")],
          "chain_ledger": [chain_tx("c11", "w0", "w1", "6750", 150), chain_tx("c12", "w0", "w2", "4500", 160),
                           chain_tx("c13", "w1", "deposit", "6750", 240), chain_tx("c14", "w2", "deposit", "4500", 250)],
          "offramp_crypto_spent": {"deposit": "10000"}, "probability": "86.5"}
    a2 = [account("victim", "Korban · BCA", "BCA", "012345670002", "VICTIM"),
          account("mule1", "Mule Tier-1 · BCA", "BCA", "0881122335"),
          account("mule2", "Mule Tier-2 · BRI", "BRI", "0332211990002"),
          account("va", "VA Tokocrypto · Demo", "Permata", "880800007500", "PAKD_VA", platform="Tokocrypto", registry_ref="DEMO-VA-REGISTRY-02")]
    initial2 = "0x71C0000000000000000000000000000000000001"
    s2 = {"id": "direct-va-loopback", "title": "FNA → PAKD VA → Arkham → Keputusan PAKD",
          "description": "VA Tokocrypto, dua hop on-chain, lalu deposit akun Indodax penadah.",
          "handoff": handoff_seed("direct-va-loopback", "BCA", "120000000", a2[0]["account"], a2[1]["account"]),
          "accounts": a2, "bank_ledger": [bank_tx("b21", "victim", "mule1", "120000000", 0),
                    bank_tx("b22", "mule1", "mule2", "120000000", 30), bank_tx("b23", "mule2", "va", "120000000", 65)],
          "onramp": {"kind": "VA", "bank_target": "va", "uid": "TKC-DEMO-077", "platform": "Tokocrypto",
                     "initial_wallet": "w0", "crypto_amount": "7500", "fiat_amount_idr": "120000000", "chain": "BSC_BEP20", "asset": "USDT",
                     "kyc": {"name": "Identitas Taker Demo 077", "document_ref": "KYC-SYNTH-077", "status": "SYNTHETIC_VERIFIED"}},
          "p2p_orders": [], "wallets": [wallet("w0", initial2, "Initial wallet · 0x71C…"),
                    wallet("w1", "0x7200000000000000000000000000000000000002", "Unhosted · 0x720…"),
                    wallet("w2", "0x7300000000000000000000000000000000000003", "Unhosted · 0x730…"),
                    wallet("deposit", "0x7400000000000000000000000000000000000004", "Indodax deposit · Demo", "DIRECT_EXCHANGE_DEPOSIT", balance="7500",
                           platform="Indodax", uid="IDX-DEMO-099", recovery_institution="PAKD")],
          "chain_ledger": [chain_tx("c21", "w0", "w1", "7500", 150, chain="BSC_BEP20"),
                    chain_tx("c22", "w1", "w2", "7500", 200, chain="BSC_BEP20"), chain_tx("c23", "w2", "deposit", "7500", 260, chain="BSC_BEP20")],
          "probability": "94"}
    a3 = [account("victim", "Korban · BRI", "BRI", "012345670003", "VICTIM"),
          account("mule1", "Mule Tier-1 · BCA", "BCA", "0881122336"),
          account("merchant", "Merchant P2P · BCA", "BCA", "2998811223", "P2P_MERCHANT", merchant_id="MCH-6630",
                  incoming_24h=96, outgoing_24h=103, exact_amount_ratio="0.96", unique_counterparties=75)]
    initial3 = "0x88B0000000000000000000000000000000000001"
    s3 = {"id": "p2p-dex-cold-wallet", "title": "FNA → PAKD P2P → DEX & Permohonan Issuer",
          "description": "15.600 USDT ERC-20: 11.000 USDT tersisa pada cold wallet dan paket permohonan issuer.",
          "handoff": handoff_seed("p2p-dex-cold-wallet", "BRI", "250000000", a3[0]["account"], a3[1]["account"]),
          "accounts": a3, "bank_ledger": [bank_tx("b31", "victim", "mule1", "250000000", 0), bank_tx("b32", "mule1", "merchant", "250000000", 45)],
          "onramp": {"kind": "P2P", "bank_target": "merchant", "uid": "TKR-00931", "platform": "PAKD Demo P2P",
                     "initial_wallet": "w0", "crypto_amount": "15600", "chain": "ETHEREUM_ERC20", "asset": "USDT",
                     "kyc": {"name": "Identitas Taker Demo 931", "document_ref": "KYC-SYNTH-931", "status": "SYNTHETIC_VERIFIED"}},
          "p2p_orders": [p2p_order("P2P-BUY-6630", "MCH-6630", a3[2]["account"], "250000000", "15600", "TKR-00931", initial3, 90)],
          "wallets": [wallet("w0", initial3, "Initial wallet · 0x88B…"),
                      wallet("cold", "0x99A0000000000000000000000000000000000002", "Cold wallet · 0x99A…", balance="11000", recovery_institution="ISSUER"),
                      wallet("dex", "0xDEa0000000000000000000000000000000000003", "Uniswap V3 · Demo", "DEX_ROUTER",
                             boundary="Swap USDT→ETH memerlukan log swap dan valuasi tambahan; atribusi dihentikan di router."),
                      wallet("dex-next", "0xA110000000000000000000000000000000000004", "Pool Uniswap · investigasi", "DEX_POOL")],
          "chain_ledger": [chain_tx("c31", "w0", "cold", "11000", 150, chain="ETHEREUM_ERC20"),
                           chain_tx("c32", "w0", "dex", "4600", 180, chain="ETHEREUM_ERC20")],
          "expansion_ledger": [chain_tx("cx31", "dex", "dex-next", "4600", 195, chain="ETHEREUM_ERC20")],
          "expansion_note": "Hanya arus masuk pool; bukan bukti kepemilikan penerima swap. Nilai recoverable tidak bertambah.",
          "fees_idr": "400000", "probability": "62"}
    # The reporting amount and USDT conversion intentionally include a disclosed fee.
    a4 = [account("victim", "Korban · BNI", "BNI", "012345670004", "VICTIM"),
          account("mule1", "Mule Tier-1 · BCA", "BCA", "0881122337"),
          account("merchant", "Merchant P2P · Mandiri", "Mandiri", "1310099887744", "P2P_MERCHANT", merchant_id="MCH-4404",
                  incoming_24h=145, outgoing_24h=152, exact_amount_ratio="0.98", unique_counterparties=111)]
    initial4 = "TGo1d3nP7rQ2sV8wY4zA6bC9dF1gH5jK3mN"
    s4 = {"id": "golden-period", "title": "FNA Golden Period · Review PAKD & Issuer",
          "description": "Rp60 juta masih di escrow dan Rp35 juta pada initial wallet; cakupan penyelamatan demo 100%.",
          "handoff": handoff_seed("golden-period", "BNI", "95000000", a4[0]["account"], a4[1]["account"]),
          "accounts": a4, "bank_ledger": [bank_tx("b41", "victim", "mule1", "95000000", 0), bank_tx("b42", "mule1", "merchant", "95000000", 25)],
          "onramp": {"kind": "P2P", "bank_target": "merchant", "uid": "TKR-00404", "platform": "PAKD Demo P2P",
                     "initial_wallet": "w0", "crypto_amount": "2187.5", "held_escrow_idr": "60000000", "chain": "TRON_TRC20", "asset": "USDT",
                     "kyc": {"name": "Identitas Taker Demo 404", "document_ref": "KYC-SYNTH-404", "status": "SYNTHETIC_VERIFIED"}},
          "p2p_orders": [p2p_order("P2P-BUY-4404", "MCH-4404", a4[2]["account"], "95000000", "5937.5", "TKR-00404", initial4, 55,
                                   released_crypto_usdt="2187.5", held_crypto_usdt="3750")],
          "wallets": [wallet("w0", initial4, "Initial wallet · golden period", balance="2187.5", recovery_institution="ISSUER")],
          "chain_ledger": [], "reported_after_minutes": 6, "probability": "98"}
    for index, scenario in enumerate((s1, s2, s3, s4), 1):
        ramp = scenario["onramp"]
        ramp["withdrawal_timestamp"] = stamp(120)
        ramp["withdrawal_tx_hash"] = ("0x" if ramp["chain"] != "TRON_TRC20" else "") + sha256((scenario["id"] + "-withdrawal").encode()).hexdigest()
        ramp["kyc"]["nik_masked"] = f"3174********{index:04d}"
        accounts = {a["id"]: a for a in scenario["accounts"]}
        snapshot_edges = scenario.pop("bank_ledger")
        last_posting = snapshot_edges[-1]
        sender, gateway = accounts[last_posting["source"]], accounts[last_posting["target"]]
        handoff = scenario["handoff"]
        handoff.update({"last_fna_pujk_name": sender["bank"], "last_fna_account_number": sender["account"],
            "pakd_gateway_va_or_account": gateway["account"], "fiat_transfer_timestamp": last_posting["timestamp"],
            "rrn_reference_number": f"RRN-DEMO-{index:04d}"})
        node_ids = {edge["source"] for edge in snapshot_edges} | {edge["target"] for edge in snapshot_edges}
        scenario["fna_snapshot"] = {"fna_trail_id": handoff["fna_trail_id"],
            "nodes": [a for a in scenario["accounts"] if a["id"] in node_ids], "edges": snapshot_edges,
            "source": "SYNTHETIC_FNA_HANDOFF_SNAPSHOT", "bank_traversal_performed_here": False}
        scenario["pakd_intake_ledger"] = [{"intake_id": f"PAKD-IN-{index:04d}", "rrn_reference_number": handoff["rrn_reference_number"],
            "gateway_account": gateway["account"], "fiat_amount_idr": handoff["fiat_transfer_amount_idr"],
            "timestamp": last_posting["timestamp"], "pakd_name": ramp["platform"], "pakd_user_uid": ramp["uid"],
            "intake_mechanism": "DIRECT_VA_DEPOSIT" if ramp["kind"] == "VA" else "P2P_ON_RAMP_ESCROW"}]
        for order in scenario["p2p_orders"]:
            order["rrn_reference_number"] = handoff["rrn_reference_number"] if order["p2p_direction"] == "ON_RAMP_DEPOSIT" else f"RRN-SELL-{index:04d}"
    next(w for w in s2["wallets"] if w["id"] == "deposit")["kyc_nik_masked"] = "3273********0099"
    s1["bank_settlements"] = [{"order_id": "P2P-SELL-9104", "source_account": "1390011223300",
        "target_account": "034101009988111", "amount_idr": "160000000", "timestamp": stamp(360), "reference": "SYNTH-STL-9104"}]
    return {s["id"]: s for s in (s1, s2, s3, s4)}

SCENARIOS = fixtures()


def get_scenario(scenario_id):
    return deepcopy(SCENARIOS[scenario_id])


def scenario_catalog():
    return {"scenarios": [{key: deepcopy(s[key]) for key in ("id", "title", "description", "handoff")} for s in SCENARIOS.values()],
            "simulation": True}
