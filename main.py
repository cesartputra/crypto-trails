"""Local IASC cross-rail proof of concept. No operational recovery connections."""
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import time
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from schemas import CrossRailGraphResponse, FreezeRequest, IASCTransactionReportInput, TrailNode, TrailEdge
from scenarios import SCENARIOS, get_scenario, scenario_catalog
from services.arkham_client import ArkhamClient, ArkhamAPIError, CHAIN_NAMES
from services.bank_trailer import BankTrailer
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.recovery_action import plan_recovery, execute_simulated_action

D = Decimal
ROOT = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, title="IASC Cross-Rail Money Trailing · PoC", version="1.0.0",
              description="Data sintetis dan aksi pemulihan simulasi. Bukan sistem operasional atau layanan resmi OJK.")
# shortcut: state lokal maksimum 128 trace selama 1 jam; gunakan penyimpanan terenkripsi/audit durable sebelum multi-worker/produksi.
TRACES: OrderedDict = OrderedDict()
MAX_TRACES = 128
TRACE_TTL_SECONDS = 3600


def store_trace(graph):
    now = time.monotonic()
    for key in [key for key, value in TRACES.items() if now - value["created"] > TRACE_TTL_SECONDS]:
        TRACES.pop(key, None)
    while len(TRACES) >= MAX_TRACES:
        TRACES.popitem(last=False)
    TRACES[graph.trace_id] = {"graph": graph, "created": now, "executions": {}, "expansions": {}}


def require_trace(trace_id):
    state = TRACES.get(trace_id)
    if not state or time.monotonic() - state["created"] > TRACE_TTL_SECONDS:
        TRACES.pop(trace_id, None)
        raise HTTPException(404, "Trace tidak ditemukan/kedaluwarsa. Lakukan pelacakan ulang.")
    return state


@app.middleware("http")
async def local_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/v1/health")
async def health():
    try:
        client = ArkhamClient()
        mode = "SIMULATION" if client.simulation else "LIVE_READ_ONLY_AVAILABLE"
    except ValueError:
        mode = "INVALID_ARKHAM_CONFIGURATION"
    return {"status": "ok", "simulation": True, "arkham_mode": mode, "recovery_mode": "SIMULATION_ONLY"}


@app.get("/api/v1/trail/scenarios")
async def scenarios():
    return scenario_catalog()


@app.post("/api/v1/trail/trace-report", response_model=CrossRailGraphResponse)
async def trace_report(report: IASCTransactionReportInput):
    trace_id = str(uuid4())
    if not report.scenario_id:
        graph = CrossRailGraphResponse(trace_id=trace_id, report_id=report.report_id, scenario_id=None,
            nodes=[TrailNode(id="victim", label=f"Korban · {report.nama_penyelenggara_korban}", node_zone="FIAT_BANK", node_type="VICTIM",
                             account=report.nomor_rekening_korban, amount_idr=report.nominal_transaksi, taint_score_pct=D(0),
                             metadata={"owner": report.nama_pemilik_rekening_korban, "source": "REPORT_INPUT_NOT_VERIFIED"}),
                   TrailNode(id="reported", label=f"Terlapor · {report.nama_penyelenggara_terlapor}", node_zone="FIAT_BANK", node_type="REPORTED_ACCOUNT",
                             account=report.nomor_rekening_terlapor, amount_idr=report.nominal_transaksi, hop=1, taint_score_pct=D(0),
                             metadata={"owner": report.nama_pemilik_rekening_terlapor, "source": "REPORT_INPUT_NOT_VERIFIED"})],
            edges=[TrailEdge(id="reported-transfer", source="victim", target="reported", edge_type="BANK_TRANSFER", label="Transfer dilaporkan · belum diverifikasi",
                             amount_idr=report.nominal_transaksi, timestamp=report.waktu_transaksi, metadata={"source": "REPORT_INPUT_NOT_VERIFIED"})],
            total_scam_idr=report.nominal_transaksi, total_traced_idr=D(0), recoverable_amount_idr=D(0), recovery_probability_pct=D(0),
            hop_count=1, warnings=["DATA GAP: input laporan belum diverifikasi. Tidak ada mutasi bank, data escrow, KYC atau bukti on-chain mitra untuk laporan ini. Pilih skenario demo untuk data sintetis."])
        store_trace(graph)
        return graph
    if report.scenario_id not in SCENARIOS:
        raise HTTPException(404, "Skenario demo tidak ditemukan")
    scenario = get_scenario(report.scenario_id)
    expected = IASCTransactionReportInput.model_validate(scenario["report"])
    # Every material input must agree with the selected synthetic ledger; report_id may be operator-assigned.
    mismatches = [key for key in IASCTransactionReportInput.model_fields if key not in {"report_id", "scenario_id"}
                  and getattr(expected, key) != getattr(report, key)]
    if mismatches:
        raise HTTPException(422, {"message": "Input tidak cocok dengan ledger skenario. Muat ulang skenario atau hapus scenario_id untuk laporan tanpa data mitra.", "fields": mismatches})
    try:
        nodes, edges, candidates = BankTrailer().trace(report, scenario)
        bridge = P2PAndPAKDBridge()
        p2p = bridge.resolve_onramp(scenario, nodes, edges, candidates)
        # Demo fixtures stay deterministic even when a live Arkham key is configured.
        ArkhamClient(api_key="").trace_fixture(scenario, nodes, edges)
        p2p.extend(bridge.resolve_offramp(scenario, nodes, edges))
        actions = plan_recovery(scenario, nodes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    recoverable = min(report.nominal_transaksi, sum((D(a.metadata.get("recoverable_contribution_idr", "0")) for a in actions), D(0)))
    warnings = ["DEMO: seluruh identitas, KYC, mutasi, label intelijen, saldo dan tx_hash sintetis. Tidak terhubung ke IASC/bank/PAKD.",
                "Probabilitas pemulihan adalah asumsi skenario untuk demo, bukan prediksi terkalibrasi atau jaminan dana kembali.",
                "Taint memakai proportional flow pada ledger sintetis lengkap; bukan penetapan pidana atau bukti tunggal kepemilikan.",
                "Semua freeze/hold adalah simulasi. Paket blacklist adalah draf permohonan issuer yang belum dikirim."]
    if scenario.get("fees_idr"):
        warnings.append(f"Biaya simulasi Rp{scenario['fees_idr']}; konversi tetap Rp16.000/USDT bukan harga pasar. Total terlacak termasuk biaya yang diketahui.")
    if scenario.get("expansion_note"):
        warnings.append("Atribusi berhenti pada DEX router: hasil swap tidak boleh diasumsikan milik pelaku tanpa log dan bukti tambahan.")
    badges = ["DIRECT VA"] if scenario["onramp"]["kind"] == "VA" else ["P2P ON-RAMP DETECTED"]
    if any(p.p2p_direction == "OFF_RAMP_WITHDRAWAL" for p in p2p):
        badges.append("P2P OFF-RAMP CASH-OUT DETECTED")
    edges.sort(key=lambda edge: edge.timestamp or report.waktu_transaksi)
    graph = CrossRailGraphResponse(trace_id=trace_id, report_id=report.report_id, scenario_id=report.scenario_id, nodes=nodes, edges=edges,
        p2p_detections=p2p, total_scam_idr=report.nominal_transaksi, total_traced_idr=report.nominal_transaksi,
        recoverable_amount_idr=recoverable, recovery_probability_pct=D(scenario["probability"]),
        total_traced_usdt=D(scenario["onramp"]["crypto_amount"]) + D(scenario["onramp"].get("held_escrow_idr", "0")) / D(16000),
        hop_count=max((n.hop for n in nodes), default=0), route_badges=badges, recovery_actions=actions, warnings=warnings)
    store_trace(graph)
    return graph


async def live_expansion(client, source, chain):
    transfers, intel = await asyncio.gather(client.get_transfers(source.address, chain), client.get_address_intelligence(source.address, chain))
    raw = transfers.get("transfers", []) if isinstance(transfers, dict) else transfers
    if not isinstance(raw, list):
        raise ArkhamAPIError("Kontrak transfer Arkham tidak didukung")
    nodes, edges = {}, []
    skipped = 0
    for index, tx in enumerate(raw[:50]):
        if not isinstance(tx, dict):
            skipped += 1
            continue
        def extract(value):
            return value.get("address") if isinstance(value, dict) else value
        from_address, to_address = extract(tx.get("fromAddress")), extract(tx.get("toAddress"))
        normalize = str.lower if chain in {"ETHEREUM_ERC20", "BSC_BEP20"} else str
        matches_source = isinstance(from_address, str) and normalize(from_address) == normalize(source.address)
        symbol = tx.get("tokenSymbol")
        transfer_chain = tx.get("chain")
        if transfer_chain not in {chain, CHAIN_NAMES[chain]}:
            skipped += 1
            continue
        if not matches_source or not isinstance(to_address, str) or not 20 <= len(to_address) <= 100 or symbol not in {"USDT", "USDC", "ETH"}:
            skipped += 1
            continue
        try:
            amount = D(str(tx["unitValue"]))
            if not amount.is_finite() or amount <= 0:
                raise ValueError()
            timestamp = tx.get("blockTimestamp")
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(timestamp / 1000 if timestamp > 100000000000 else timestamp, timezone.utc)
            elif isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            else:
                raise ValueError()
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError()
        except (InvalidOperation, KeyError, ValueError, OverflowError, OSError):
            skipped += 1
            continue
        tx_hash = tx.get("transactionHash")
        if not isinstance(tx_hash, str) or not tx_hash or len(tx_hash) > 160:
            skipped += 1
            continue
        node_id = "live-" + sha256((chain + normalize(to_address)).encode()).hexdigest()[:16]
        nodes[node_id] = TrailNode(id=node_id, label=to_address[:10] + "…", node_zone="ONCHAIN_WALLET", node_type="LIVE_UNATTRIBUTED_WALLET",
            entity_label="Unverified on-chain address", address=to_address, chain=chain, asset=symbol, crypto_amount=amount,
            taint_score_pct=None, hop=source.hop + 1, status="LIVE_UNATTRIBUTED",
            metadata={"evidence_source": "ARKHAM_LIVE_READ_ONLY", "ownership": "UNKNOWN", "balance_status": "NOT_FETCHED", "taint_status": "UNKNOWN"})
        edges.append(TrailEdge(id="live-" + sha256((tx_hash + str(index)).encode()).hexdigest()[:16], source=source.id, target=node_id,
            edge_type="ONCHAIN_TRANSFER", label=f"{amount} {symbol} · live belum teratribusi", crypto_amount=amount, asset=symbol,
            tx_hash=tx_hash, timestamp=timestamp, metadata={"evidence_source": "ARKHAM_LIVE_READ_ONLY", "taint_status": "UNKNOWN", "chain": chain}))
    return {"nodes": list(nodes.values()), "edges": edges, "simulation": False,
            "source_intelligence": intel, "warnings": ["LIVE READ ONLY: hasil belum teratribusi ke dana laporan. Tidak menambah recovery/taint dan tidak membuktikan kepemilikan.",
                "Satu halaman maksimal 50 outgoing transfers; hasil bukan riwayat lengkap. Kontrak provider perlu diverifikasi terhadap akses API Anda.", f"Record dilewati karena tidak didukung/tidak valid: {skipped}."]}


@app.get("/api/v1/trail/address/{address}/expand")
async def expand_address(address: str, trace_id: str = Query(min_length=1, max_length=80),
                         chain: str = Query(default="TRON_TRC20"), live: bool = False):
    state = require_trace(trace_id)
    graph = state["graph"]
    source = next((n for n in graph.nodes if n.address == address and n.chain == chain), None)
    if not source:
        raise HTTPException(404, "Address/jaringan tidak ada dalam trace ini")
    if chain not in CHAIN_NAMES:
        raise HTTPException(422, "Jaringan tidak didukung")
    cache_key = (address, chain, live)
    if cache_key in state["expansions"]:
        return state["expansions"][cache_key]
    if len(graph.nodes) >= 256 or len(graph.edges) >= 512:
        raise HTTPException(422, "Batas ukuran graph tercapai; mulai investigasi baru")
    if live:
        try:
            client = ArkhamClient()
            if client.simulation:
                raise HTTPException(409, "ARKHAM_API_KEY belum diatur; gunakan perluasan simulasi")
            result = await live_expansion(client, source, chain)
        except ArkhamAPIError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(503, "Konfigurasi Arkham tidak valid") from exc
    else:
        if not graph.scenario_id:
            result = {"nodes": [], "edges": [], "warnings": ["Tidak ada fixture on-chain untuk laporan ini."], "simulation": True}
        else:
            nodes, edges = ArkhamClient(api_key="").expand_fixture(get_scenario(graph.scenario_id), address, chain, {n.id for n in graph.nodes})
            result = {"nodes": nodes, "edges": edges, "simulation": True,
                      "warnings": ["Tidak ada outgoing hop tambahan dalam fixture ini." if not edges else "Hop tambahan adalah arus investigasi; tidak menambah nilai dana terselamatkan."]}
    existing_ids = {n.id for n in graph.nodes}
    result["nodes"] = result["nodes"][:max(256 - len(graph.nodes), 0)]
    allowed_ids = existing_ids | {n.id for n in result["nodes"]}
    result["edges"] = [edge for edge in result["edges"] if edge.source in allowed_ids and edge.target in allowed_ids][:max(512 - len(graph.edges), 0)]
    for node in result["nodes"]:
        node.hop = source.hop + 1
    graph.nodes.extend(n for n in result["nodes"] if n.id not in existing_ids)
    edge_ids = {e.id for e in graph.edges}
    graph.edges.extend(e for e in result["edges"] if e.id not in edge_ids)
    state["expansions"][cache_key] = result
    return result


@app.post("/api/v1/recovery/execute-freeze")
async def execute_freeze(request: FreezeRequest):
    state = require_trace(request.trace_id)
    if request.action_id in state["executions"]:
        return state["executions"][request.action_id]
    graph = state["graph"]
    action = next((a for a in graph.recovery_actions if a.id == request.action_id), None)
    if not action:
        raise HTTPException(404, "Aksi tidak ditemukan dalam trace ini")
    try:
        result = execute_simulated_action(graph, action)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state["executions"][request.action_id] = result
    return result


@app.get("/")
async def dashboard():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
