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
from schemas import CrossRailGraphResponse, FNAMoneyTrailHandoffInput, SendFreezeRequest, SimulateInstitutionDecisionInput, TrailNode, TrailEdge
from scenarios import SCENARIOS, get_scenario, scenario_catalog
from services.arkham_client import ArkhamClient, ArkhamAPIError, CHAIN_NAMES
from services.bank_trailer import FNAHandoffAdapter
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.recovery_action import plan_governance_requests, build_evidence_packet, send_request, decide_request, GovernanceConflict

D = Decimal
ROOT = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, title="IASC Cross-Rail Money Trailing · PoC", version="2.0.0",
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
        client = ArkhamClient()
        mode = "SIMULATION" if client.simulation else "LIVE_READ_ONLY_AVAILABLE"
    except ValueError:
        mode = "INVALID_ARKHAM_CONFIGURATION"
    return {"status": "ok", "simulation": True, "arkham_mode": mode, "governance_mode": "SIMULATED_DISPATCH_AND_INSTITUTION_DECISIONS", "fna_mode": "IMPORTED_HANDOFF_ONLY"}


@app.get("/api/v1/trail/scenarios")
async def scenarios():
    return scenario_catalog()


@app.post("/api/v1/trail/trace-report", response_model=CrossRailGraphResponse)
async def trace_report(handoff: FNAMoneyTrailHandoffInput):
    trace_id = str(uuid4())
    if not handoff.scenario_id:
        nodes, edges = FNAHandoffAdapter().manual_snapshot(handoff)
        graph = CrossRailGraphResponse(trace_id=trace_id, iasc_ticket_id=handoff.iasc_ticket_id, fna_trail_id=handoff.fna_trail_id,
            scenario_id=None, fna_handoff=handoff, nodes=nodes, edges=edges,
            total_scam_idr=handoff.fiat_transfer_amount_idr, total_traced_idr=D(0), recoverable_amount_idr=D(0),
            recovery_probability_pct=D(0), hop_count=max(n.hop for n in nodes),
            warnings=["DATA GAP: handoff belum diverifikasi. Tidak ada snapshot FNA atau ledger PAKD mitra untuk memvalidasi RRN, gateway, UID/KYC atau withdrawal. Tidak ada rekomendasi freeze dibuat."])
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
        # Fixture FNA/PAKD/Arkham stays reproducible; a configured key only enables opt-in live expansion.
        ArkhamClient(api_key="").trace_fixture(scenario, nodes, edges)
        offramp, deposits = bridge.resolve_offramp(scenario, nodes, edges)
        p2p.extend(offramp)
        pakd.extend(deposits)
        requests = plan_governance_requests(scenario, nodes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    recoverable = min(handoff.fiat_transfer_amount_idr, sum((D(r.metadata["available_amount_idr"]) for r in requests), D(0)))
    warnings = ["DEMO: snapshot FNA, ledger PAKD, identitas/KYC tersamarkan, label Arkham, saldo dan tx_hash adalah sintetis; tidak ada konektor operasional FNA/IASC/bank/PAKD.",
        "Modul ini menerima handoff downstream FNA dan menambahkan PAKD Trails serta Arkham On-Chain Trails; tidak menggantikan pelacakan PUJK oleh FNA.",
        "IASC hanya merekomendasikan penundaan. Keputusan dan pelaksanaan freeze berada pada masing-masing PUJK/PAKD atau issuer terkait.",
        "Nilai potensial berbeda dari nominal disetujui institusi. Pengiriman tiket tidak membekukan dana; seluruh callback keputusan di sini simulasi.",
        "Probabilitas recovery dan taint memakai asumsi fixture, bukan kepastian pidana, prediksi terkalibrasi atau jaminan pengembalian dana."]
    if scenario.get("fees_idr"):
        warnings.append(f"Biaya konversi fixture Rp{scenario['fees_idr']}; kurs tetap Rp16.000/USDT bukan harga pasar.")
    if scenario.get("expansion_note"):
        warnings.append("Atribusi berhenti pada DEX; output swap tidak diasumsikan milik pelaku tanpa bukti tambahan.")
    badges = ["FNA HANDOFF", "PAKD TRAILS", "DIRECT VA" if scenario["onramp"]["kind"] == "VA" else "P2P ON-RAMP DETECTED"]
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
