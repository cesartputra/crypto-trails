"""Local IASC cross-rail proof of concept. No operational recovery connections."""
from collections import OrderedDict
from decimal import Decimal
from html import escape
from pathlib import Path
import time
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from schemas import CrossRailGraphResponse, FNAMoneyTrailHandoffInput, SendFreezeRequest, SimulateInstitutionDecisionInput, LiveWalletTrailInput, PAKDAPICallLog, TrailNode, TrailEdge
from scenarios import SCENARIOS, get_scenario, scenario_catalog
from services.etherscan_client import EtherscanClient, EtherscanAPIError, _config
from services.multichain_scanner import MultichainScanner, ScannerAPIError
from services.cryptoapis_continuation import CryptoAPIsAPIError
from services.cryptoapis_config import config as cryptoapis_config, CryptoAPIsConfigurationError
from services.pakd_api_consumer import PAKDAPIConsumer, PAKDAPIError
from services.pakd_scoped_mirror_provider import router as pakd_mirror_router
from services.chains import CHAIN_CONFIG, normalize_chain, explorer_address_url, explorer_tx_url
from services.scenario_trailer import ScenarioTrailer
from services.bank_trailer import FNAHandoffAdapter
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.recovery_action import plan_governance_requests, build_evidence_packet, send_request, decide_request, GovernanceConflict

D = Decimal
ROOT = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, title="In-House OJK Crypto-Trails · PoC", version="5.0.0",
              description="PAKD Scoped Mirror REST API sintetis + scanner BTC/Solana/EVM live read-only. Tata kelola freeze simulasi; bukan sistem resmi OJK.")
# shortcut: state lokal maksimum 128 trace selama 1 jam; gunakan penyimpanan terenkripsi/audit durable sebelum multi-worker/produksi.
TRACES: OrderedDict = OrderedDict()
app.include_router(pakd_mirror_router)
MAX_TRACES = 128
TRACE_TTL_SECONDS = 3600


@app.exception_handler(RequestValidationError)
async def validation_error_without_request_secrets(request, exc):
    # Pydantic's input/context can contain the UI API key.
    return JSONResponse(status_code=422, content={"detail": [
        {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
        for error in exc.errors()]})


def store_trace(graph):
    stamp_chain_evidence(graph.nodes, graph.edges)
    now = time.monotonic()
    for key in [key for key, value in TRACES.items() if now - value["created"] > TRACE_TTL_SECONDS]:
        TRACES.pop(key, None)
    while len(TRACES) >= MAX_TRACES:
        TRACES.popitem(last=False)
    TRACES[graph.trace_id] = {"graph": graph, "created": now, "packets": {}, "expansions": {}}


def stamp_chain_evidence(nodes, edges):
    for node in nodes:
        if node.chain:
            config = CHAIN_CONFIG[normalize_chain(node.chain)]
            node.chain_id = config["chain_id"]
            node.chain_family = config["chain_family"]
            node.scanner_provider = node.scanner_provider or node.metadata.get("scanner_provider") or config["scanner_provider"]
            node.chain_branch = node.chain_branch or normalize_chain(node.chain)
            if node.address:
                node.explorer_url = explorer_address_url(node.chain, node.address) or ""
    for edge in edges:
        edge.chain = edge.chain or edge.metadata.get("chain")
        if edge.chain:
            config = CHAIN_CONFIG[normalize_chain(edge.chain)]
            edge.chain_id = config["chain_id"]
            edge.chain_family = config["chain_family"]
            edge.scanner_provider = edge.scanner_provider or edge.metadata.get("scanner_provider") or config["scanner_provider"]
            edge.chain_branch = edge.chain_branch or normalize_chain(edge.chain)
            if edge.tx_hash:
                edge.explorer_url = explorer_tx_url(edge.chain, edge.tx_hash) or ""


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
    solana_mode = "SOLSCAN_PRO_API" if _config().get("SOLSCAN_API_KEY", "").strip() else "SOLANA_MAINNET_RPC"
    return {"status": "ok", "simulation": True, "live_provider": mode,
        "live_hop_strategy": {"initial_max_hops": 3, "continuation_provider": "CRYPTOAPIS", "max_hops": 6},
        "cryptoapis_configured": bool(cryptoapis_config()['CRYPTOAPIS_API_KEY']),
        "dedicated_scanners": {"BTC": "MEMPOOL_SPACE_WITH_BLOCKSTREAM_FALLBACK", "SOLANA": solana_mode, "EVM": mode},
        "mirror_access": "SCOPED_REST_API_ASGI_DEMO", "supported_chains": list(CHAIN_CONFIG), "governance_mode": "SIMULATED_DISPATCH_AND_INSTITUTION_DECISIONS",
        "fna_mode": "IMPORTED_HANDOFF_ONLY"}


@app.get("/api/v1/mirror-db/records")
async def mirror_records():
    try:
        return await PAKDAPIConsumer().get_schema_preview_api()
    except PAKDAPIError as exc:
        raise HTTPException(502, str(exc)) from None


@app.post("/api/v1/trail/live-scanner", response_model=CrossRailGraphResponse)
@app.post("/api/v1/trail/live-etherscan", response_model=CrossRailGraphResponse)
async def trace_live_wallet(payload: LiveWalletTrailInput):
    try:
        result = await MultichainScanner().trace_wallet(
            payload.wallet_address, max_hops=payload.max_hops, token_filter=payload.token_filter, direction=payload.direction,
            source_chain=payload.source_chain, auto_scan_cross_chain=payload.auto_scan_cross_chain, etherscan_api_key=payload.etherscan_api_key)
    except (EtherscanAPIError, ScannerAPIError, CryptoAPIsAPIError, PAKDAPIError) as exc:
        raise HTTPException(502, str(exc)) from None
    except CryptoAPIsConfigurationError as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError:
        raise HTTPException(503, "Konfigurasi explorer tidak valid; periksa host resmi pada .env") from None
    nodes, edges = result["nodes"], result["edges"]
    warnings = list(result["warnings"])
    handoff = None
    if payload.simulate_fna_and_mirror_origin:
        normalize = str.lower if CHAIN_CONFIG[payload.source_chain]["chain_family"] == "EVM" else str
        seed = next(node for node in nodes if node.address and normalize(node.address) == normalize(payload.wallet_address) and normalize_chain(node.chain) == payload.source_chain)
        nodes[:0] = [
            TrailNode(id="live-origin-bank", label="FNA Money Trails · hulu simulasi", node_zone="FIAT_BANK",
                node_type="SIMULATED_FNA_ORIGIN", hop=0, metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True}),
            TrailNode(id="live-origin-mirror", label="PAKD Mirror API · hulu simulasi", node_zone="CRYPTO_EXCHANGE_BRIDGE",
                node_type="SIMULATED_MIRROR_ORIGIN", hop=1, metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True,
                    "origin_note": "Ilustrasi hubungan FNA → Mirror DB → wallet pilihan; bukan hasil respons API PAKD atau withdrawal terverifikasi."})]
        for node in nodes[2:]:
            node.hop += 2
        edges[:0] = [
            TrailEdge(id="live-origin-handoff", source="live-origin-bank", target="live-origin-mirror", edge_type="SIMULATED_FNA_HANDOFF",
                label="Handoff ilustratif · simulasi", metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True}),
            TrailEdge(id="live-origin-withdrawal", source="live-origin-mirror", target=seed.id, edge_type="SIMULATED_WITHDRAWAL",
                label="Asal wallet ilustratif · simulasi", metadata={"evidence_source": "SIMULATED_ORIGIN", "simulation": True})]
        warnings.insert(0, "HULU SIMULASI: hubungan bank/PAKD dengan seed wallet hanya ilustrasi, bukan bukti kepemilikan, respons API PAKD atau withdrawal nyata.")
    graph = CrossRailGraphResponse(trace_id=str(uuid4()), iasc_ticket_id="LIVE-READ-ONLY", fna_trail_id="",
        scenario_id=None, simulation=False, fna_handoff=handoff, nodes=nodes, edges=edges,
        total_scam_idr=D(0), total_traced_idr=D(0), recoverable_amount_idr=D(0), recovery_probability_pct=D(0),
        hop_count=max((node.hop for node in nodes), default=0), route_badges=[payload.source_chain + " LIVE", "PAKD MIRROR API", "AUTO CROSS-CHAIN SCAN" if payload.auto_scan_cross_chain else "SINGLE SOURCE CHAIN"],
        warnings=warnings, live_metadata={**result["metadata"], "simulated_origin": payload.simulate_fna_and_mirror_origin},
        pakd_api_exchange_logs=result.get("pakd_api_exchange_logs", []), chains_used=sorted({normalize_chain(node.chain) for node in nodes if node.chain}))
    store_trace(graph)
    return graph


@app.get("/api/v1/trail/scenarios")
async def scenarios():
    return scenario_catalog()


@app.post("/api/v1/trail/trace-report", response_model=CrossRailGraphResponse)
async def trace_report(handoff: FNAMoneyTrailHandoffInput):
    trace_id = str(uuid4())
    consumer = PAKDAPIConsumer()
    if not handoff.scenario_id:
        nodes, edges = FNAHandoffAdapter().manual_snapshot(handoff)
        try:
            match = await consumer.consume_pakd_inflow_lookup_api(handoff.pakd_gateway_va_or_account,
                handoff.rrn_reference_number, handoff.fiat_transfer_amount_idr)
        except PAKDAPIError as exc:
            raise HTTPException(502, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if match:
            gateway = next(node for node in nodes if node.id == "gateway")
            gateway.label = "PAKD Mirror API · kecocokan inflow"
            gateway.metadata["mirror_deposit"] = match
        warnings = ["HANDOFF MANUAL BELUM DIVERIFIKASI: tidak ada snapshot FNA yang membuktikan asal dana. Tidak ada rekomendasi freeze atau ledger skenario dibuat.",
            "Hasil API scoped dari seed sintetis tersedia pada gateway; UID/initial wallet adalah referensi API PAKD, bukan bukti hubungan perkara." if match else
            "DATA GAP: VA/RRN/nominal tidak cocok dengan rekaman Mirror DB PAKD."]
        graph = CrossRailGraphResponse(trace_id=trace_id, iasc_ticket_id=handoff.iasc_ticket_id, fna_trail_id=handoff.fna_trail_id,
            scenario_id=None, fna_handoff=handoff, nodes=nodes, edges=edges,
            total_scam_idr=handoff.fiat_transfer_amount_idr, total_traced_idr=D(0), recoverable_amount_idr=D(0),
            recovery_probability_pct=D(0), hop_count=max(n.hop for n in nodes),
            warnings=warnings, pakd_api_exchange_logs=consumer.logs)
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
        bridge = P2PAndPAKDBridge(consumer=consumer)
        p2p, pakd = await bridge.resolve_onramp(handoff, scenario, nodes, edges)
        # Preset tetap sintetis; pelacakan mainnet adalah aksi live terpisah.
        ScenarioTrailer().trace_fixture(scenario, nodes, edges)
        offramp, deposits = await bridge.resolve_offramp(scenario, nodes, edges)
        p2p.extend(offramp)
        pakd.extend(deposits)
        requests = plan_governance_requests(scenario, nodes)
    except PAKDAPIError as exc:
        raise HTTPException(502, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    recoverable = min(handoff.fiat_transfer_amount_idr, sum((D(r.metadata["available_amount_idr"]) for r in requests), D(0)))
    warnings = ["DEMO: snapshot FNA, Scoped Mirror API PAKD, identitas/KYC tersamarkan, ledger on-chain, saldo dan tx_hash adalah sintetis; tidak ada konektor operasional FNA/IASC/bank/PAKD. Gunakan Live Wallet Tracker untuk transaksi mainnet asli.",
        "Modul ini menerima handoff downstream FNA dan menambahkan PAKD Trails serta Multichain & Bridge Trails; tidak menggantikan pelacakan PUJK oleh FNA.",
        "IASC hanya merekomendasikan penundaan. Keputusan dan pelaksanaan freeze berada pada masing-masing PUJK/PAKD atau issuer terkait.",
        "Nilai potensial berbeda dari nominal disetujui institusi. Pengiriman tiket tidak membekukan dana; seluruh callback keputusan di sini simulasi.",
        "Probabilitas recovery dan taint memakai asumsi fixture, bukan kepastian pidana, prediksi terkalibrasi atau jaminan pengembalian dana."]
    if scenario.get("fees_idr"):
        warnings.append(f"Biaya konversi fixture Rp{scenario['fees_idr']}; kurs tetap Rp16.000/USDT bukan harga pasar.")
    if scenario.get("expansion_note"):
        warnings.append("Atribusi berhenti pada DEX; output swap tidak diasumsikan milik pelaku tanpa bukti tambahan.")
    badges = ["FNA HANDOFF", "PAKD MIRROR API", "DIRECT VA" if scenario["onramp"]["kind"] == "VA" else "P2P ON-RAMP DETECTED"]
    if scenario.get("dispersal_offramp") or any(p.p2p_direction == "OFF_RAMP_WITHDRAWAL" for p in p2p):
        badges.append("P2P OFF-RAMP CASH-OUT DETECTED")
    edges.sort(key=lambda edge: edge.timestamp or handoff.fiat_transfer_timestamp)
    graph = CrossRailGraphResponse(trace_id=trace_id, iasc_ticket_id=handoff.iasc_ticket_id, fna_trail_id=handoff.fna_trail_id,
        scenario_id=handoff.scenario_id, fna_handoff=handoff, nodes=nodes, edges=edges, pakd_trails=pakd,
        p2p_detections=p2p, total_scam_idr=handoff.fiat_transfer_amount_idr, total_traced_idr=handoff.fiat_transfer_amount_idr,
        recoverable_amount_idr=recoverable, recovery_probability_pct=D(scenario["probability"]),
        total_traced_usdt=sum((D(item["crypto_amount"]) for item in scenario["chain_dispersals"] if item["token_symbol"] == "USDT"), D(0)) if scenario.get("multi_chain_dispersal") else D(scenario["onramp"]["crypto_amount"]) + D(scenario["onramp"].get("held_escrow_idr", "0")) / D(16000),
        hop_count=max((n.hop for n in nodes), default=0), route_badges=badges, governance_requests=requests, warnings=warnings,
        pakd_api_exchange_logs=consumer.logs, chain_dispersals=scenario.get("chain_dispersals", []),
        chains_used=list(dict.fromkeys(item["chain"] for item in scenario.get("chain_dispersals", []))))
    store_trace(graph)
    return graph


@app.get("/api/v1/trail/address/{address}/expand")
async def expand_address(address: str, trace_id: str = Query(min_length=1, max_length=80),
                         chain: str = Query(default="TRON_TRC20"), live: bool = False):
    state = require_trace(trace_id)
    graph = state["graph"]
    try:
        canonical = normalize_chain(chain)
    except ValueError:
        raise HTTPException(422, "Jaringan tidak didukung") from None
    normalize = str.lower if CHAIN_CONFIG[canonical]["chain_family"] == "EVM" or canonical == "BTC" and address.lower().startswith("bc1") else str
    source = next((n for n in graph.nodes if n.address and normalize(n.address) == normalize(address) and normalize_chain(n.chain) == canonical), None)
    if not source:
        raise HTTPException(404, "Address/jaringan tidak ada dalam trace ini")
    cache_key = (address, chain, live)
    if cache_key in state["expansions"]:
        return state["expansions"][cache_key]
    if len(graph.nodes) >= 256 or len(graph.edges) >= 512:
        raise HTTPException(422, "Batas ukuran graph tercapai; mulai investigasi baru")
    if live:
        if canonical == "TRON":
            raise HTTPException(422, "TRON hanya tersedia pada preset sintetis; live scanner mendukung BTC, Solana dan EVM")
        if source.metadata.get('wallet_hop', 0) >= 6:
            raise HTTPException(422, "Batas 6 hop wallet tercapai; mulai trace baru untuk melanjutkan")
        try:
            result = await MultichainScanner().expand_wallet(graph, source)
            live_seed = next(node for node in result["nodes"] if node.address and normalize(node.address) == normalize(source.address) and normalize_chain(node.chain) == canonical)
            result["nodes"] = [node for node in result["nodes"] if node.id != live_seed.id]
            for edge in result["edges"]:
                if edge.source == live_seed.id:
                    edge.source = source.id
                if edge.target == live_seed.id:
                    edge.target = source.id
            result["simulation"] = False
        except (EtherscanAPIError, ScannerAPIError, CryptoAPIsAPIError, PAKDAPIError) as exc:
            raise HTTPException(502, str(exc)) from None
        except CryptoAPIsConfigurationError as exc:
            raise HTTPException(503, str(exc)) from None
        except ValueError:
            raise HTTPException(503, "Konfigurasi explorer tidak valid") from None
    else:
        if not graph.scenario_id:
            result = {"nodes": [], "edges": [], "warnings": ["Tidak ada fixture on-chain untuk laporan ini."], "simulation": True}
        else:
            nodes, edges = ScenarioTrailer().expand_fixture(get_scenario(graph.scenario_id), address, chain, {n.id for n in graph.nodes})
            result = {"nodes": nodes, "edges": edges, "simulation": True,
                      "warnings": ["Tidak ada outgoing hop tambahan dalam fixture ini." if not edges else "Hop tambahan adalah arus investigasi; tidak menambah nilai dana terselamatkan."]}
    stamp_chain_evidence(result["nodes"], result["edges"])
    existing_ids = {n.id for n in graph.nodes}
    result["nodes"] = result["nodes"][:max(256 - len(graph.nodes), 0)]
    allowed_ids = existing_ids | {n.id for n in result["nodes"]}
    result["edges"] = [edge for edge in result["edges"] if edge.source in allowed_ids and edge.target in allowed_ids][:max(512 - len(graph.edges), 0)]
    if not live or graph.simulation:
        for node in result["nodes"]:
            node.hop = source.hop + 1
    graph.nodes.extend(n for n in result["nodes"] if n.id not in existing_ids)
    edge_ids = {e.id for e in graph.edges}
    graph.edges.extend(e for e in result["edges"] if e.id not in edge_ids)
    if live and not graph.simulation:
        metadata, added = graph.live_metadata, result.get('metadata', {})
        providers = [*metadata.get('provider', '').split(', '), *added.get('provider', '').split(', ')]
        metadata['provider'] = metadata['scanner_provider'] = ', '.join(dict.fromkeys(filter(None, providers)))
        metadata['requests_made'] = metadata.get('requests_made', 0) + added.get('expansion_requests_made', 0)
        metadata['cryptoapis_requests_made'] = metadata.get('cryptoapis_requests_made', 0) + added.get('expansion_cryptoapis_requests_made', 0)
        metadata['wallet_hops_traced'] = max((n.metadata.get('wallet_hop', 0) for n in graph.nodes), default=0)
        graph.hop_count = max((n.hop for n in graph.nodes), default=0)
    graph.pakd_api_exchange_logs.extend(PAKDAPICallLog.model_validate(log) for log in result.get("pakd_api_exchange_logs", []))
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


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def local_api_reference():
    rows = "".join(f"<tr><td>{escape(method.upper())}</td><td>{escape(path)}</td><td>{escape(operation.get('summary', ''))}</td></tr>"
        for path, methods in app.openapi()["paths"].items() for method, operation in methods.items())
    return '<!doctype html><html lang="id"><head><meta charset="utf-8"><title>Crypto-Trails API Reference</title><link rel="stylesheet" href="/static/styles.css"></head><body><main><h1>Crypto-Trails · OpenAPI Reference</h1><p>Endpoint provider PAKD dan consumer OJK. <a href="/openapi.json">Buka kontrak OpenAPI JSON lengkap</a></p><table class="mirror-table"><thead><tr><th>Method</th><th>Endpoint</th><th>Operasi</th></tr></thead><tbody>' + rows + '</tbody></table></main></body></html>'


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
