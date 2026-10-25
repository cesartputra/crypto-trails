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
    s2 = {"id": "direct-va-loopback", "title": "FNA → Mirror DB PAKD → Loopback Ethereum → Keputusan PAKD",
          "description": "VA Tokocrypto, dua hop on-chain, lalu deposit akun Indodax penadah.",
          "handoff": handoff_seed("direct-va-loopback", "BCA", "120000000", a2[0]["account"], a2[1]["account"]),
          "accounts": a2, "bank_ledger": [bank_tx("b21", "victim", "mule1", "120000000", 0),
                    bank_tx("b22", "mule1", "mule2", "120000000", 30), bank_tx("b23", "mule2", "va", "120000000", 65)],
          "onramp": {"kind": "VA", "bank_target": "va", "uid": "TKC-DEMO-077", "platform": "Tokocrypto",
                     "initial_wallet": "w0", "crypto_amount": "7500", "fiat_amount_idr": "120000000", "chain": "ETHEREUM_ERC20", "asset": "USDT",
                     "kyc": {"name": "Identitas Taker Demo 077", "document_ref": "KYC-SYNTH-077", "status": "SYNTHETIC_VERIFIED"}},
          "p2p_orders": [], "wallets": [wallet("w0", initial2, "Initial wallet · 0x71C…"),
                    wallet("w1", "0x7200000000000000000000000000000000000002", "Unhosted · 0x720…"),
                    wallet("w2", "0x7300000000000000000000000000000000000003", "Unhosted · 0x730…"),
                    wallet("deposit", "0x7400000000000000000000000000000000000004", "Indodax deposit · Demo", "DIRECT_EXCHANGE_DEPOSIT", balance="7500",
                           platform="Indodax", uid="IDX-DEMO-099", recovery_institution="PAKD")],
          "chain_ledger": [chain_tx("c21", "w0", "w1", "7500", 150, chain="ETHEREUM_ERC20"),
                    chain_tx("c22", "w1", "w2", "7500", 200, chain="ETHEREUM_ERC20"), chain_tx("c23", "w2", "deposit", "7500", 260, chain="ETHEREUM_ERC20")],
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
    bridge_presets = []
    for index, destination, source_pakd, receiving_pakd, protocol, contract in (
        (5, "ARBITRUM", "Tokocrypto", "Indodax", "Stargate Finance (LayerZero)", "0x8731d54e9d02c286767d56ac03e8037c07e01e98"),
        (6, "BSC", "Pintu", "Reku", "Celer cBridge", "0x5427fefa711eff984124bfbb1ab6fbf5e3da1820")):
        scenario = deepcopy(s2)
        scenario_id = f"bridge-ethereum-{destination.lower()}"
        scenario.update(id=scenario_id, title=f"FNA → {source_pakd} API → {protocol} → {destination} → {receiving_pakd}",
            description=f"Bridge sintetis Ethereum ke {destination}, hop lanjutan dan reverse lookup Scoped Mirror API {receiving_pakd}.",
            bridge_scenario=True)
        source_uid, target_uid = f"{source_pakd[:3].upper()}-BRIDGE-{index:03d}", f"{receiving_pakd[:3].upper()}-BRIDGE-{index:03d}"
        source_address = "0x" + f"{index * 100 + 1:040x}"
        landing_address = "0x" + f"{index * 100 + 2:040x}"
        hop_address = "0x" + f"{index * 100 + 3:040x}"
        deposit_address = "0x" + f"{index * 100 + 4:040x}"
        gateway_number = f"88080000{index}500"
        scenario["handoff"].update(scenario_id=scenario_id, iasc_ticket_id=f"IASC-DEMO-BRIDGE-{index}",
            fna_trail_id=f"FNA-DEMO-BRIDGE-{index}", pakd_gateway_va_or_account=gateway_number,
            rrn_reference_number=f"RRN-BRIDGE-{index:04d}")
        scenario["onramp"].update(platform=source_pakd, uid=source_uid, chain="ETHEREUM",
            withdrawal_tx_hash="0x"+sha256((scenario_id+"-withdrawal").encode()).hexdigest(),
            kyc={"name":f"Nasabah Bridge Demo {index}", "document_ref":f"KYC-BRIDGE-{index}",
                 "status":"SYNTHETIC_VERIFIED", "nik_masked":f"3174********{index:04d}"})
        for records in (scenario["accounts"], scenario["fna_snapshot"]["nodes"]):
            for row in records:
                if row["id"]=="va":row.update(label=f"VA {source_pakd} · Demo",platform=source_pakd,account=gateway_number)
        scenario["fna_snapshot"]["fna_trail_id"]=scenario["handoff"]["fna_trail_id"]
        scenario["pakd_intake_ledger"][0].update(intake_id=f"PAKD-IN-BRIDGE-{index}",gateway_account=gateway_number,
            rrn_reference_number=scenario["handoff"]["rrn_reference_number"],pakd_name=source_pakd,pakd_user_uid=source_uid)
        scenario["wallets"]=[
            wallet("w0",source_address,"Initial wallet · Ethereum",chain="ETHEREUM"),
            wallet("bridge",contract,f"🌉 {protocol} · ETH → {destination}","BRIDGE_PROTOCOL",chain="ETHEREUM",
                is_bridge_node=True,bridge_protocol_name=protocol,bridged_from_chain="ETHEREUM",bridged_to_chain=destination),
            wallet("landing",landing_address,f"Landing wallet · {destination}",chain=destination),
            wallet("w1",hop_address,f"Hop lanjutan · {destination}",chain=destination),
            wallet("deposit",deposit_address,f"{receiving_pakd} deposit · {destination}","DIRECT_EXCHANGE_DEPOSIT",balance="7500",
                platform=receiving_pakd,uid=target_uid,recovery_institution="PAKD",chain=destination,kyc_nik_masked=f"3273********{index:04d}")]
        bridge_tx=chain_tx(f"bridge-{index}-pivot","bridge","landing","7500",180,chain=destination)
        bridge_tx.update(edge_type="CROSS_CHAIN_BRIDGE",bridge_protocol_name=protocol,bridged_from_chain="ETHEREUM",
            bridged_to_chain=destination,bridge_evidence_status="SIMULATED_CORRELATED_BRIDGE")
        scenario["chain_ledger"]=[chain_tx(f"bridge-{index}-enter","w0","bridge","7500",150,chain="ETHEREUM"),
            bridge_tx,chain_tx(f"bridge-{index}-after","landing","w1","7500",210,chain=destination),
            chain_tx(f"bridge-{index}-loopback","w1","deposit","7500",260,chain=destination)]
        bridge_presets.append(scenario)
    dispersal = multichain_dispersal_fixture()
    all_scenarios = (double_diamond_fixture(), dispersal, s1, s2, s3, s4, *bridge_presets)
    for scenario in all_scenarios:
        if "chain_dispersals" not in scenario:
            ramp = scenario["onramp"]
            chain = {"ETHEREUM_ERC20":"ETHEREUM", "BSC_BEP20":"BSC", "TRON_TRC20":"TRON"}.get(ramp["chain"], ramp["chain"])
            initial = next(w for w in scenario["wallets"] if w["id"] == ramp["initial_wallet"])
            scenario["chain_dispersals"] = [{"withdrawal_id": "WD-"+scenario["id"], "chain":chain,
                "chain_family":"TRON" if chain=="TRON" else "EVM", "scanner_provider":"TRON_SCENARIO" if chain=="TRON" else "ETHERSCAN_V2",
                "token_symbol":ramp["asset"], "crypto_amount":ramp["crypto_amount"],
                "fiat_value_idr":str(D(ramp["crypto_amount"])*RATE),"initial_wallet_address":initial["address"],
                "withdrawal_tx_hash":ramp["withdrawal_tx_hash"],"withdrawal_timestamp":ramp["withdrawal_timestamp"],
                "initial_node_id":initial["id"],"endpoint_node_id":None,"endpoint_status":"TRACED_SCENARIO"}]
    return {scenario["id"]: scenario for scenario in all_scenarios}


def _base58(data):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(data,"big"); encoded = ""
    while number:
        number, remainder = divmod(number,58); encoded = alphabet[remainder]+encoded
    return "1"*(len(data)-len(data.lstrip(b"\0")))+encoded


def _demo_btc_address(label):
    payload = b"\0"+bytes.fromhex(sha256(label.encode()).hexdigest())[:20]
    checksum = bytes.fromhex(sha256(bytes.fromhex(sha256(payload).hexdigest())).hexdigest())[:4]
    return _base58(payload+checksum)


def multichain_dispersal_fixture():
    case_id = "multichain-dispersal"
    a = [account("victim","Korban · BNI","BNI","012345670010","VICTIM"),
         account("mule1","Penampung · BCA","BCA","0881122340"),
         account("va","VA Tokocrypto","Permata","880800018000","PAKD_VA",platform="Tokocrypto"),
         account("final-sol-bank","Pencairan SOL · BRI","BRI","034101009988222","FINAL_CASHOUT",available_balance_idr="45000000")]
    h = handoff_seed(case_id,"BNI","180000000",a[0]["account"],a[1]["account"])
    h.update(last_fna_pujk_name="BCA",last_fna_account_number=a[1]["account"],pakd_gateway_va_or_account=a[2]["account"],
        fiat_transfer_timestamp=stamp(40),rrn_reference_number="RRN-DISPERSAL-001")
    solhop = _base58(bytes.fromhex(sha256(b"demo-sol-hop").hexdigest()))
    soldeposit = _base58(bytes.fromhex(sha256(b"demo-sol-pintu").hexdigest()))
    branches = [
        {"withdrawal_id":"WD-DISP-BTC","chain":"BTC","chain_family":"BITCOIN","scanner_provider":"MEMPOOL_SPACE", "token_symbol":"BTC",
         "crypto_amount":"0.065","fiat_value_idr":"70000000","initial_wallet_address":"bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", "initial_node_id":"btc-initial","endpoint_node_id":"btc-deposit","endpoint_status":"LOOPBACK_PAKD_INDODAX"},
        {"withdrawal_id":"WD-DISP-SOL","chain":"SOLANA","chain_family":"SOLANA","scanner_provider":"SOLANA_MAINNET_RPC", "token_symbol":"SOL",
         "crypto_amount":"185","fiat_value_idr":"50000000","initial_wallet_address":"5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d", "initial_node_id":"sol-initial","endpoint_node_id":"final-sol-bank","endpoint_status":"P2P_OFFRAMP_BRI"},
        {"withdrawal_id":"WD-DISP-ETH","chain":"ETHEREUM","chain_family":"EVM","scanner_provider":"ETHERSCAN_V2", "token_symbol":"USDT",
         "crypto_amount":"3750","fiat_value_idr":"60000000","initial_wallet_address":"0x71C0000000000000000000000000000000000010", "initial_node_id":"eth-initial","endpoint_node_id":"eth-cold","endpoint_status":"ISSUER_REVIEW_REQUIRED"}]
    for index, branch in enumerate(branches):
        branch["withdrawal_timestamp"] = stamp(120+index)
        digest = bytes.fromhex(sha256(branch["withdrawal_id"].encode()).hexdigest())
        branch["withdrawal_tx_hash"] = _base58(digest+digest) if branch["chain"]=="SOLANA" else ("0x" if branch["chain"]=="ETHEREUM" else "")+digest.hex()
    wallets = [
        wallet("btc-initial",branches[0]["initial_wallet_address"],"BTC awal",chain="BTC",asset="BTC"),
        wallet("btc-hop",_demo_btc_address("demo-btc-hop"),"Perantara BTC",chain="BTC",asset="BTC"),
        wallet("btc-deposit",_demo_btc_address("demo-indodax-btc"),"Deposit Indodax","DIRECT_EXCHANGE_DEPOSIT",balance="0.065",chain="BTC",asset="BTC",platform="Indodax",uid="IDX-DISP-BTC",recovery_institution="PAKD",kyc_nik_masked="3273********0010"),
        wallet("sol-initial",branches[1]["initial_wallet_address"],"SOL awal",chain="SOLANA",asset="SOL"),
        wallet("sol-hop",solhop,"Perantara SOL",chain="SOLANA",asset="SOL"),
        wallet("sol-deposit",soldeposit,"Deposit Pintu","DIRECT_EXCHANGE_DEPOSIT",balance="0",chain="SOLANA",asset="SOL",platform="Pintu",uid="PTU-DISP-SOL",recovery_institution="PAKD",kyc_nik_masked="3273********0011"),
        wallet("eth-initial",branches[2]["initial_wallet_address"],"USDT awal",chain="ETHEREUM",asset="USDT"),
        wallet("eth-cold","0x99A0000000000000000000000000000000000010","Unhosted USDT",balance="3750",chain="ETHEREUM",asset="USDT",recovery_institution="ISSUER")]
    ledger=[]
    for branch, route in [(branches[0],["btc-initial","btc-hop","btc-deposit"]),(branches[1],["sol-initial","sol-hop","sol-deposit"]),(branches[2],["eth-initial","eth-cold"])]:
        for index,(source,target) in enumerate(zip(route,route[1:])):
            key=branch["withdrawal_id"]+f"-hop-{index}"
            digest=bytes.fromhex(sha256(key.encode()).hexdigest())
            ledger.append({"id":key,"source":source,"target":target,"chain":branch["chain"],"asset":branch["token_symbol"],"crypto_amount":branch["crypto_amount"],
                "timestamp":stamp(150+index*60),"tx_hash":_base58(digest+digest) if branch["chain"]=="SOLANA" else ("0x" if branch["chain"]=="ETHEREUM" else "")+digest.hex(),"chain_branch":branch["chain"]})
    ramp = {"kind":"VA","bank_target":"va","uid":"TKC-DISP-001","platform":"Tokocrypto","initial_wallet":"eth-initial", "crypto_amount":"3750", "fiat_amount_idr":"180000000", "chain":"ETHEREUM", "asset":"USDT",
        "withdrawal_timestamp":branches[2]["withdrawal_timestamp"],"withdrawal_tx_hash":branches[2]["withdrawal_tx_hash"],"kyc":{"name":"Nasabah Demo Dispersal","nik_masked":"3174********0010","status":"SYNTHETIC_VERIFIED"}}
    return {"id":case_id,"title":"Sebaran Multi-Chain: BTC, Solana & Ethereum","description":"Rp180 juta dari Tokocrypto tersebar ke BTC Rp70 juta, SOL Rp50 juta dan USDT Ethereum Rp60 juta.",
        "multi_chain_dispersal":True,
        "dispersal_offramp":{"deposit_node_id":"sol-deposit","final_bank_node":"final-sol-bank","crypto_amount":"185","fiat_amount_idr":"50000000", "order_id":"P2P-DISP-SOL-001", "sell_timestamp":stamp(260),"settlement_timestamp":stamp(300),"settlement_reference":"BRI-DISP-SYNTH-001"},
        "handoff":h,"accounts":a,"onramp":ramp,"chain_dispersals":branches,"wallets":wallets,"chain_ledger":ledger,"p2p_orders":[],"probability":"86",
        "fna_snapshot":{"fna_trail_id":h["fna_trail_id"],"nodes":a[:3],"edges":[bank_tx("disp-bank-1","victim","mule1","180000000",0),bank_tx("disp-bank-2","mule1","va","180000000",40)],"source":"SYNTHETIC_FNA_HANDOFF_SNAPSHOT","bank_traversal_performed_here":False},
        "pakd_intake_ledger":[{"intake_id":"PAKD-IN-DISP-001","rrn_reference_number":h["rrn_reference_number"],"gateway_account":h["pakd_gateway_va_or_account"],"fiat_amount_idr":"180000000","timestamp":stamp(40),"pakd_name":"Tokocrypto","pakd_user_uid":ramp["uid"],"intake_mechanism":"DIRECT_VA_DEPOSIT"}]}



def double_diamond_fixture():
    case_id, uid = "double-diamond-loopback", "TKO-9988112"
    accounts = [
        account("victim", "Rekening Korban (BNI - 0192837465)", "BNI", "0192837465", "VICTIM", chronological_step=0),
        account("primary", "Rekening Scammer Utama Tier-1 (BCA - 8810293847 a.n. Sindikat Utama)", "BCA", "8810293847", chronological_step=1),
    ]
    intakes, orders = [], []
    for index, bank, number, amount in ((1, "Mandiri", "13000998811", "80000000"),
            (2, "BRI", "034101002233", "75000000"), (3, "Permata", "4120088776", "75000000"),
            (4, "CIMB Niaga", "7055443322", "70000000")):
        source = f"mule{index}"
        accounts.append(account(source, f"Bank Mule {index} ({bank} - {number})", bank, number, chronological_step=2))
        mechanism = "DIRECT_VA_DEPOSIT" if index <= 2 else "P2P_ON_RAMP_ESCROW"
        gateway = f"88080099881{index}" if index <= 2 else f"77000099881{index}"
        rrn = f"RRN-DOUBLE-{index}"
        intakes.append({"intake_id": f"DD-IN-{index}", "source_node_id": source, "gateway_account": gateway,
            "rrn_reference_number": rrn, "fiat_amount_idr": amount, "crypto_amount": str(D(amount)/RATE),
            "timestamp": stamp(55 + index*5), "pakd_name": "Tokocrypto", "pakd_user_uid": uid,
            "intake_mechanism": mechanism})
        if index > 2:
            orders.append(p2p_order(f"DD-P2P-{index}", f"DD-MERCHANT-{index}", gateway, amount, str(D(amount)/RATE),
                uid, "0x9988112000000000000000000000000000000009", 55+index*5,
                platform="Tokocrypto", rrn_reference_number=rrn, source_node_id=source))
    handoff = handoff_seed(case_id, "BNI", "300000000", accounts[0]["account"], accounts[1]["account"])
    # This handoff is a composite FNA case summary, never an invented single bank posting.
    handoff.update(last_fna_pujk_name="BCA", last_fna_account_number=accounts[1]["account"],
        pakd_gateway_va_or_account=uid, fiat_transfer_timestamp=stamp(75), rrn_reference_number="RRN-DOUBLE-SUMMARY")
    ramp = {"kind": "MIXED", "uid": uid, "platform": "Tokocrypto", "initial_wallet": "hop1a",
        "crypto_amount": "18750", "fiat_amount_idr": "300000000", "chain": "ETHEREUM_ERC20", "asset": "USDT",
        "withdrawal_timestamp": stamp(120), "withdrawal_tx_hash": "0x"+sha256(b"DD-WD-a").hexdigest(),
        "kyc": {"name": "Identitas Sindikat Utama Demo", "nik_masked": "3171********0009",
                "document_ref": "KYC-SYNTH-DD", "status": "SYNTHETIC_VERIFIED"}}
    wallets, withdrawals = [], []
    for index, letter, prefix, suffix, amount in ((0,"a","71C8","10a1","5000"), (1,"b","82D9","20b2","4750"),
            (2,"c","93EA","30c3","4500"), (3,"d","A4FB","40d4","4500")):
        address = "0x"+prefix+"0"*32+suffix
        wallets.append(wallet("hop1"+letter, address, f"ETH Wallet Hop-1{letter.upper()} (0x{prefix}...{suffix})", chronological_step=4))
        withdrawals.append({"withdrawal_id": "DD-WD-"+letter, "chain": "ETHEREUM", "chain_family": "EVM",
            "scanner_provider": "ETHERSCAN_V2", "token_symbol": "USDT", "crypto_amount": amount,
            "fiat_value_idr": str(D(amount)*RATE), "initial_wallet_address": address,
            "withdrawal_tx_hash": "0x"+sha256(("DD-WD-"+letter).encode()).hexdigest(),
            "withdrawal_timestamp": stamp(120+index), "initial_node_id": "hop1"+letter,
            "endpoint_node_id": "scammer-uid", "endpoint_status": "SAME_UID_FULL_CIRCLE_LOOPBACK"})
    for letter, prefix, suffix in (("a","B501","50e5"), ("b","C612","60f6"), ("c","D723","70a7"), ("d","E834","80b8")):
        wallets.append(wallet("hop2"+letter, "0x"+prefix+"0"*32+suffix,
            f"ETH Wallet Hop-2{letter.upper()} (0x{prefix}...{suffix})", chronological_step=5))
    wallets.append(wallet("scammer-uid", "0x9988112000000000000000000000000000000009",
        "Deposit Return · Akun PAKD Tokocrypto TKO-9988112", "DIRECT_EXCHANGE_DEPOSIT", balance="18750",
        platform="Tokocrypto", uid=uid, kyc_nik_masked="3171********0009", recovery_institution="PAKD", chronological_step=3))
    routes = [("a","a","3000"),("a","b","2000"),("b","b","2750"),("b","c","2000"),
              ("c","c","2500"),("c","d","2000"),("d","d","2500"),("d","a","2000")]
    ledger = []
    for index, (source, target, amount) in enumerate(routes):
        tx = chain_tx(f"DD-LAYER-{index}", "hop1"+source, "hop2"+target, amount, 180+index, chain="ETHEREUM_ERC20")
        tx["chronological_step"] = 5
        ledger.append(tx)
    for index, letter, amount in ((0,"a","5000"),(1,"b","4750"),(2,"c","4500"),(3,"d","4500")):
        tx = chain_tx("DD-RETURN-"+letter, "hop2"+letter, "scammer-uid", amount, 240+index, chain="ETHEREUM_ERC20")
        tx.update(chronological_step=6, same_uid_loopback=True)
        ledger.append(tx)
    return {"id": case_id, "title": "Skenario Konvergensi & Loopback: Korban ➔ Scammer ➔ Banyak Bank Mule ➔ 1 Akun PAKD (VA & P2P) ➔ Banyak Wallet ETH (Multi-Hop) ➔ Kembali ke 1 Akun PAKD",
        "description": "Pola Berlian Ganda: Rp300 juta, 4 bank mule, 2 VA + 2 P2P, 4 wallet ETH Hop 1 dan 4 Hop 2; 100% kembali ke UID Tokocrypto yang sama. Satu rekomendasi freeze terpadu, belum dikirim.",
        "converging_intakes": intakes, "same_uid_loopback": True, "accounts": accounts, "handoff": handoff,
        "fna_snapshot": {"fna_trail_id": handoff["fna_trail_id"], "nodes": accounts,
            "edges": [bank_tx("DD-BANK-ORIGIN", "victim", "primary", "300000000", 0),
                *[bank_tx(f"DD-BANK-{i}", "primary", item["source_node_id"], item["fiat_amount_idr"], 30+i)
                  for i, item in enumerate(intakes, 1)]],
            "source": "SYNTHETIC_FNA_HANDOFF_SNAPSHOT", "bank_traversal_performed_here": False,
            "handoff_scope": "COMPOSITE_CASE_SUMMARY"},
        "pakd_intake_ledger": [{"intake_id": "DD-IN-SUMMARY", "rrn_reference_number": handoff["rrn_reference_number"],
            "gateway_account": uid, "fiat_amount_idr": "300000000", "timestamp": stamp(75), "pakd_name": "Tokocrypto",
            "pakd_user_uid": uid, "intake_mechanism": "COMPOSITE_VA_P2P_INFLOW"}],
        "onramp": ramp, "p2p_orders": orders, "wallets": wallets, "chain_ledger": ledger,
        "chain_dispersals": withdrawals, "probability": "94"}



SCENARIOS = fixtures()


def get_scenario(scenario_id):
    return deepcopy(SCENARIOS[scenario_id])


def scenario_catalog():
    return {"scenarios": [{key: deepcopy(s[key]) for key in ("id", "title", "description", "handoff")} for s in SCENARIOS.values()],
            "simulation": True}
