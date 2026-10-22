"""Local IASC cross-rail proof of concept. No operational recovery connections."""
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
import time
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from schemas import CrossRailGraphResponse, FNAMoneyTrailHandoffInput, SendFreezeRequest, SimulateInstitutionDecisionInput, LiveWalletTrailInput, TrailNode, TrailEdge
from scenarios import SCENARIOS, get_scenario, scenario_catalog
from services.etherscan_client import EtherscanClient, EtherscanAPIError
from services.pakd_mirror_db import mirror_db
from services.scenario_trailer import ScenarioTrailer
from services.bank_trailer import FNAHandoffAdapter
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.recovery_action import plan_governance_requests, build_evidence_packet, send_request, decide_request, GovernanceConflict

D = Decimal
ROOT = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, title="In-House OJK Crypto-Trails · PoC", version="3.0.0",
              description="PAKD Mirror DB SQLite sintetis + Ethereum Mainnet live read-only. Tata kelola freeze simulasi; bukan sistem resmi OJK.")
# shortcut: state lokal maksimum 128 trace selama 1 jam; gunakan penyimpanan terenkripsi/audit durable sebelum multi-worker/produksi.
TRACES: OrderedDict = OrderedDict()
MAX_TRACES = 128
TRACE_TTL_SECONDS = 3600


@app.exception_handler(RequestValidationError)
async def validation_error_without_request_secrets(request, exc):
    # Pydantic's input/context can contain the UI API key.
    return JSONResponse(status_code=422, content={"detail": [
        {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
        for error in exc.errors()]})


def store_trace(graph):
    now = time.monotonic()
    for key in [key for key, value in TRACES.items() if now - value["created"] > TRACE_TTL_SECONDS]:
        TRACES.pop(key, None)
    while len(TRACES) >= MAX_TRACES:
        TRACES.popitem(last=False)
    TRACES[graph.trace_id] = {"graph": graph, "created": now, "packets": {}, "expansions": {}}


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
        client = EtherscanClient()
        mode = "ETHERSCAN" if client.api_key else "BLOCKSCOUT_PUBLIC"
    except ValueError:
        mode = "INVALID_ETHERSCAN_CONFIGURATION"
    return {"status": "ok", "simulation": True, "live_provider": mode,
        "mirror_db": "SQLITE_SYNTHETIC_SEEDED", "governance_mode": "SIMULATED_DISPATCH_AND_INSTITUTION_DECISIONS",
        "fna_mode": "IMPORTED_HANDOFF_ONLY"}


@app.get("/api/v1/mirror-db/records")
async def mirror_records():
    return mirror_db.get_mirror_db_preview()


@app.post("/api/v1/trail/live-etherscan", response_model=CrossRailGraphResponse)
async def trace_live_wallet(payload: LiveWalletTrailInput):
    try:
        result = await EtherscanClient(api_key=payload.etherscan_api_key).trace_real_wallet_on_etherscan(
            payload.wallet_address, max_hops=payload.max_hops, token_filter=payload.token_filter, direction=payload.direction)
    except EtherscanAPIError as exc:
        raise HTTPException(502, str(exc)) from None
    except ValueError:
        raise HTTPException(503, "Konfigurasi explorer tidak valid; periksa host resmi pada .env") from None
    nodes, edges = result["nodes"], result["edges"]
    warnings = list(result["warnings"])
    handoff = None
    if payload.simulate_fna_and_mirror_origin:
        seed = next(node for node in nodes if node.address and node.address.lower() == payload.wallet_address.lower())
        nodes[:0] = [
            TrailNode(id="live-origin-bank", label="FNA Money Trails · hulu simulasi", node_zone="FIAT_BANK",
                node_type="SIMULATED_FNA_ORIGIN", hop=0, metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True}),
            TrailNode(id="live-origin-mirror", label="PAKD Mirror DB · hulu simulasi", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="SIMULATED_MIRROR_ORIGIN", hop=1, metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True,
                    "origin_note": "Ilustrasi hubungan FNA → Mirror DB → wallet pilihan; bukan hasil JOIN SQL atau withdrawal terverifikasi."})]
        for node in nodes[2:]:
            node.hop += 2
        edges[:0] = [
            TrailEdge(id="live-origin-handoff", source="live-origin-bank", target="live-origin-mirror", edge_type="SIMULATED_FNA_HANDOFF",
                label="Handoff ilustratif · simulasi", metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True}),
            TrailEdge(id="live-origin-withdrawal", source="live-origin-mirror", target=seed.id, edge_type="SIMULATED_WITHDRAWAL",
                label="Asal wallet ilustratif · simulasi", metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True})]
        warnings.insert(0, "HULU SIMULASI: hubungan bank/PAKD dengan seed wallet hanya ilustrasi, bukan bukti kepemilikan, JOIN Mirror DB atau withdrawal nyata.")
    graph = CrossRailGraphResponse(trace_id=str(uuid4()), iasc_ticket_id="LIVE-READ-ONLY", fna_trail_id="",
        scenario_id=None, simulation=False, fna_handoff=handoff, nodes=nodes, edges=edges,
        total_scam_idr=D(0), total_traced_idr=D(0), recoverable_amount_idr=D(0), recovery_probability_pct=D(0),
        hop_count=max((node.hop for node in nodes), default=0), route_badges=["ETHEREUM MAINNET LIVE", "PAKD MIRROR REVERSE LOOKUP"],
        warnings=warnings, live_metadata={**result["metadata"], "simulated_origin": payload.simulate_fna_and_mirror_origin})
    store_trace(graph)
    return graph


@app.get("/api/v1/trail/scenarios")
async def scenarios():
    return scenario_catalog()


@app.post("/api/v1/trail/trace-report", response_model=CrossRailGraphResponse)
async def trace_report(handoff: FNAMoneyTrailHandoffInput):
    trace_id = str(uuid4())
    if not handoff.scenario_id:
        nodes, edges = FNAHandoffAdapter().manual_snapshot(handoff)
        try:
            match = mirror_db.lookup_deposit_by_fna_handoff(handoff.pakd_gateway_va_or_account,
                handoff.rrn_reference_number, handoff.fiat_transfer_amount_idr)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if match:
            gateway = next(node for node in nodes if node.id == "gateway")
            gateway.label = "PAKD Mirror DB · kecocokan SQL"
            gateway.metadata["mirror_deposit"] = match
        warnings = ["HANDOFF MANUAL BELUM DIVERIFIKASI: tidak ada snapshot FNA yang membuktikan asal dana. Tidak ada rekomendasi freeze atau ledger skenario dibuat.",
            "Hasil JOIN SQL dari seed sintetis tersedia pada gateway; UID/initial wallet adalah referensi Mirror DB, bukan bukti hubungan perkara." if match else
            "DATA GAP: VA/RRN/nominal tidak cocok dengan rekaman Mirror DB PAKD."]
        graph = CrossRailGraphResponse(trace_id=trace_id, iasc_ticket_id=handoff.iasc_ticket_id, fna_trail_id=handoff.fna_trail_id,
            scenario_id=None, fna_handoff=handoff, nodes=nodes, edges=edges,
            total_scam_idr=handoff.fiat_transfer_amount_idr, total_traced_idr=D(0), recoverable_amount_idr=D(0),
            recovery_probability_pct=D(0), hop_count=max(n.hop for n in nodes),
            warnings=warnings)
        store_trace(graph)
        return graph
    if handoff.scenario_id not in SCENARIOS:
        raise HTTPException(404, "Skenario demo tidak ditemukan")
    scenario = get_scenario(handoff.scenario_id)
    expected = FNAMoneyTrailHandoffInput.model_validate(scenario["handoff"])
    mismatches = [key for key in FNAMoneyTrailHandoffInput.model_fields if key not in {"iasc_ticket_id", "scenario_id"}
                  and getattr(expected, key) != getattr(handoff, key)]
    if mismatches:
        raise HTTPException(422, {"message": "Handoff tidak cocok dengan snapshot FNA/ledger PAKD skenario. Muat ulang preset atau kosongkan scenario_id untuk handoff manual.", "fields": mismatches})
    try:
        nodes, edges = FNAHandoffAdapter().import_snapshot(handoff, scenario)
        bridge = P2PAndPAKDBridge()
        p2p, pakd = bridge.resolve_onramp(handoff, scenario, nodes, edges)
        # Preset tetap sintetis; pelacakan mainnet adalah aksi live terpisah.
        ScenarioTrailer().trace_fixture(scenario, nodes, edges)
        offramp, deposits = bridge.resolve_offramp(scenario, nodes, edges)
        p2p.extend(offramp)
        pakd.extend(deposits)
        requests = plan_governance_requests(scenario, nodes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    recoverable = min(handoff.fiat_transfer_amount_idr, sum((D(r.metadata["available_amount_idr"]) for r in requests), D(0)))
    warnings = ["DEMO: snapshot FNA, SQLite Mirror DB PAKD, identitas/KYC tersamarkan, ledger on-chain, saldo dan tx_hash adalah sintetis; tidak ada konektor operasional FNA/IASC/bank/PAKD. Gunakan Live Wallet Tracker untuk transaksi mainnet asli.",
        "Modul ini menerima handoff downstream FNA dan menambahkan PAKD Trails serta Etherscan-compatible On-Chain Trails; tidak menggantikan pelacakan PUJK oleh FNA.",
        "IASC hanya merekomendasikan penundaan. Keputusan dan pelaksanaan freeze berada pada masing-masing PUJK/PAKD atau issuer terkait.",
        "Nilai potensial berbeda dari nominal disetujui institusi. Pengiriman tiket tidak membekukan dana; seluruh callback keputusan di sini simulasi.",
        "Probabilitas recovery dan taint memakai asumsi fixture, bukan kepastian pidana, prediksi terkalibrasi atau jaminan pengembalian dana."]
    if scenario.get("fees_idr"):
        warnings.append(f"Biaya konversi fixture Rp{scenario['fees_idr']}; kurs tetap Rp16.000/USDT bukan harga pasar.")
    if scenario.get("expansion_note"):
        warnings.append("Atribusi berhenti pada DEX; output swap tidak diasumsikan milik pelaku tanpa bukti tambahan.")
    badges = ["FNA HANDOFF", "PAKD MIRROR DB", "DIRECT VA" if scenario["onramp"]["kind"] == "VA" else "P2P ON-RAMP DETECTED"]
    if any(p.p2p_direction == "OFF_RAMP_WITHDRAWAL" for p in p2p):
        badges.append("P2P OFF-RAMP CASH-OUT DETECTED")
    edges.sort(key=lambda edge: edge.timestamp or handoff.fiat_transfer_timestamp)
    graph = CrossRailGraphResponse(trace_id=trace_id, iasc_ticket_id=handoff.iasc_ticket_id, fna_trail_id=handoff.fna_trail_id,
        scenario_id=handoff.scenario_id, fna_handoff=handoff, nodes=nodes, edges=edges, pakd_trails=pakd,
        p2p_detections=p2p, total_scam_idr=handoff.fiat_transfer_amount_idr, total_traced_idr=handoff.fiat_transfer_amount_idr,
        recoverable_amount_idr=recoverable, recovery_probability_pct=D(scenario["probability"]),
        total_traced_usdt=D(scenario["onramp"]["crypto_amount"]) + D(scenario["onramp"].get("held_escrow_idr", "0")) / D(16000),
        hop_count=max((n.hop for n in nodes), default=0), route_badges=badges, governance_requests=requests, warnings=warnings)
    store_trace(graph)
    return graph


@app.get("/api/v1/trail/address/{address}/expand")
async def expand_address(address: str, trace_id: str = Query(min_length=1, max_length=80),
                         chain: str = Query(default="TRON_TRC20"), live: bool = False):
    state = require_trace(trace_id)
    graph = state["graph"]
    normalize = str.lower if chain in {"ETHEREUM_ERC20", "BSC_BEP20"} else str
    source = next((n for n in graph.nodes if n.address and normalize(n.address) == normalize(address) and n.chain == chain), None)
    if not source:
        raise HTTPException(404, "Address/jaringan tidak ada dalam trace ini")
    if chain not in {"TRON_TRC20", "ETHEREUM_ERC20", "BSC_BEP20"}:
        raise HTTPException(422, "Jaringan tidak didukung")
    cache_key = (address, chain, live)
    if cache_key in state["expansions"]:
        return state["expansions"][cache_key]
    if len(graph.nodes) >= 256 or len(graph.edges) >= 512:
        raise HTTPException(422, "Batas ukuran graph tercapai; mulai investigasi baru")
    if live:
        if chain != "ETHEREUM_ERC20":
            raise HTTPException(422, "Live explorer hanya mendukung Ethereum Mainnet; TRON/BSC hanya tersedia pada preset sintetis")
        try:
            result = await EtherscanClient().trace_real_wallet_on_etherscan(source.address, max_hops=1)
            live_seed = next(node for node in result["nodes"] if node.address.lower() == source.address.lower())
            result["nodes"] = [node for node in result["nodes"] if node.id != live_seed.id]
            for edge in result["edges"]:
                if edge.source == live_seed.id:
                    edge.source = source.id
                if edge.target == live_seed.id:
                    edge.target = source.id
            result["simulation"] = False
        except EtherscanAPIError as exc:
            raise HTTPException(502, str(exc)) from None
        except ValueError:
            raise HTTPException(503, "Konfigurasi explorer tidak valid") from None
    else:
        if not graph.scenario_id:
            result = {"nodes": [], "edges": [], "warnings": ["Tidak ada fixture on-chain untuk laporan ini."], "simulation": True}
        else:
            nodes, edges = ScenarioTrailer().expand_fixture(get_scenario(graph.scenario_id), address, chain, {n.id for n in graph.nodes})
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


def require_governance_request(state, request_id):
    request = next((r for r in state["graph"].governance_requests if r.request_id == request_id), None)
    if request is None:
        raise HTTPException(404, "Request tidak ditemukan dalam trace ini")
    return request


@app.post("/api/v1/governance/send-freeze-request")
async def dispatch_freeze_request(payload: SendFreezeRequest):
    state = require_trace(payload.trace_id)
    graph = state["graph"]
    request = require_governance_request(state, payload.request_id)
    try:
        if request.request_id not in state["packets"]:
            packet = build_evidence_packet(graph, request)
            send_request(graph, request)
            state["packets"][request.request_id] = packet
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"request": request, "simulation": True,
        "message": "Tiket rekomendasi tercatat pada outbox simulasi. Institusi penerima memutuskan; belum ada dana diamankan oleh pengiriman tiket.",
        "evidence_packet": state["packets"][request.request_id], "confirmed_frozen_amount_idr": str(graph.confirmed_frozen_amount_idr)}


@app.post("/api/v1/governance/simulate-institution-decision")
async def simulate_institution_decision(payload: SimulateInstitutionDecisionInput):
    state = require_trace(payload.trace_id)
    graph = state["graph"]
    request = require_governance_request(state, payload.request_id)
    try:
        decide_request(graph, request, payload)
    except GovernanceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"request": request, "simulation": True,
        "message": f"Callback keputusan simulasi dicatat dari {request.decided_by_unit}; IASC tidak mengeksekusi freeze sepihak.",
        "confirmed_frozen_amount_idr": str(graph.confirmed_frozen_amount_idr)}


@app.post("/api/v1/recovery/execute-freeze", deprecated=True)
async def retired_direct_freeze():
    raise HTTPException(410, "Direct auto-freeze dihapus. Gunakan /api/v1/governance/send-freeze-request lalu /api/v1/governance/simulate-institution-decision; keputusan berada pada institusi penerima.")


@app.get("/")
async def dashboard():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
