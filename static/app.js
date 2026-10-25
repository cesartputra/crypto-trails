"use strict";

const $ = (selector) => document.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";
const state = { scenarios: [], scenarioId: null, graph: null, selected: null, selectedEdge: null, mode: "scenario", positions: new Map(), scale: 1, x: 0, y: 0, animation: 0, request: 0, packets: new Map(), busyRequests: new Set(), selectedRequestId: null, decisionDrafts: new Map(), logs: [], baseWidth: 1160, baseHeight: 650, viewportWidth: 0, viewportHeight: 0, chainFilter: "ALL", timeRange: "ALL", viewNodes: [], viewEdges: [], branchByNode: new Map(), lanes: [], currentStep: 3, focusMainPath: false, investigationView: "flow", flowProjection: null, focalId: null, pins: new Map(), graphEngine: null, layoutModel: null, physicsFrozen: false, flowMode: "ALL", groupMode: "CHAIN", timelineRange: null, sortTime: true, zoomBehavior: null, usingD3: false };
const idr = (value) => new Intl.NumberFormat("id-ID", { style: "currency", currency: "IDR", minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(Number(value || 0));
const number = (value, digits = 2) => new Intl.NumberFormat("id-ID", { maximumFractionDigits: digits }).format(Number(value || 0));
const short = (value, size = 20) => String(value || "").length > size ? `${String(value).slice(0, size - 2)}…` : String(value || "");
const friendly = (value) => String(value || "").replaceAll("_", " ");
let toastTimer;

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function svgEl(tag, attrs = {}, text) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const target = $("#toast");
  target.textContent = message;
  target.classList.toggle("error", error);
  target.hidden = false;
  toastTimer = setTimeout(() => { target.hidden = true; }, error ? 9000 : 5000);
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  let payload;
  try { payload = await response.json(); } catch { throw new Error("Server tidak mengirim JSON. Pastikan FastAPI berjalan."); }
  if (!response.ok) {
    const detail = payload.detail;
    const message = Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join(".") || "Input"}: ${item.msg}`).join(" · ") : typeof detail === "string" ? detail : detail?.message || "Permintaan tidak dapat diproses.";
    throw new Error(message);
  }
  return payload;
}

function log(message) {
  state.logs.unshift({ time: new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Jakarta" }), message });
  state.logs = state.logs.slice(0, 12);
  const list = $("#activity-log");
  list.replaceChildren(...state.logs.map((entry) => { const row = el("li"); row.append(el("time", "", entry.time), el("span", "", entry.message)); return row; }));
}

function download(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = el("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function drawScenarios() {
  $("#scenario-count").textContent = `${state.scenarios.length} KASUS`;
  $("#scenarios").replaceChildren(...state.scenarios.map((scenario, index) => {
    const button = el("button", `scenario-card${state.scenarioId === scenario.id ? " active" : ""}`);
    button.type = "button";
    button.title = scenario.title;
    button.setAttribute("aria-pressed", String(state.scenarioId === scenario.id));
    const copy = el("span", "scenario-copy");
    copy.append(el("strong", "", scenario.title), el("small", "", scenario.description));
    button.append(el("span", "scenario-index", String(index + 1).padStart(2, "0")), copy, el("span", "scenario-arrow", "↗"));
    button.addEventListener("click", () => chooseScenario(scenario));
    return button;
  }));
}

function toJakartaInput(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "").slice(0, 19);
  return new Date(date.getTime() + 7 * 3600000).toISOString().slice(0, 19);
}

async function chooseScenario(scenario) {
  setMode("scenario");
  stopAnimation();
  state.scenarioId = scenario.id;
  $("#manual-button").textContent = "+ Input handoff manual";
  $("#manual-button").classList.remove("active");
  $("#form-note").textContent = "Snapshot FNA sintetis. Pencocokan PAKD memakai RRN, gateway, nominal, dan waktu transfer.";
  for (const [name, value] of Object.entries(scenario.handoff)) {
    const input = $("#report-form").elements.namedItem(name);
    if (input) input.value = name === "fiat_transfer_timestamp" ? toJakartaInput(value) : value ?? "";
  }
  drawScenarios();
  await trace();
}

function setMode(mode) {
  state.mode = mode;
  for (const name of ["scenario", "live"]) {
    const active = mode === name;
    $(`#${name}-panel`).hidden = !active;
    $(`#${name}-tab`).setAttribute("aria-selected", String(active));
    $(`#${name}-tab`).tabIndex = active ? 0 : -1;
  }
}

function beginTrace(live = false) {
  const requestId = ++state.request;
  stopAnimation();
  clearTimeout(timelineFilterTimer);
  state.graphEngine?.stop(); state.graphEngine = null; state.focalId = null; state.pins = new Map(); state.timelineRange = null;
  if ($("#inspector-drawer")) $("#inspector-drawer").hidden = true;
  $("#entity-search").value=""; $("#entity-search-results").replaceChildren(); $("#entity-search-results").hidden=true; $("#graph-tooltip").hidden=true;
  $("#activity-timeline").replaceChildren(); $("#focal-pill-label").textContent="No active trace"; $("#visualizer-status").textContent="Waiting for evidence";
  $("#timeline-range-label").textContent="No active trace"; $("#timeline-start").disabled=$("#timeline-end").disabled=true;
  state.graph = null;
  state.positions = new Map(); state.layoutModel = null;
  state.flowProjection = null; state.viewNodes = []; state.viewEdges = [];
  $("#audit-trail-body")?.replaceChildren();
  if ($("#audit-trail-empty")) $("#audit-trail-empty").hidden = false;
  state.selected = null;
  state.selectedEdge = null;
  state.packets.clear();
  state.decisionDrafts.clear();
  state.busyRequests.clear();
  state.selectedRequestId = null;
  $("#nodes").replaceChildren();
  $("#edges").replaceChildren();
  $("#minimap-content")?.replaceChildren();
  if ($("#minimap-window")) $("#minimap-window").style.visibility = "hidden";
  if ($("#graph-node-count")) $("#graph-node-count").textContent = "—";
  if ($("#graph-edge-count")) $("#graph-edge-count").textContent = "—";
  $("#route-badges").replaceChildren();
  $("#swimlanes").replaceChildren();
  $("#chain-dispersal-cards").replaceChildren();
  $("#chain-filters").replaceChildren();
  $("#transaction-body")?.replaceChildren();
  if ($("#transaction-count")) $("#transaction-count").textContent = "Menunggu data";
  if ($("#transaction-empty")) $("#transaction-empty").hidden = false;
  if ($("#graph-empty")) $("#graph-empty").hidden = true;
  if ($("#case-title")) $("#case-title").textContent = live ? "Mengambil aktivitas wallet…" : "Menyusun alur dana…";
  if ($("#case-description")) $("#case-description").textContent = "Menunggu respons sumber data penelusuran.";
  $("#executive-title").textContent = live ? "Mengambil aktivitas blockchain…" : "Menyusun ringkasan perkara…";
  $("#executive-narrative").textContent = "Menunggu respons sumber data.";
  state.chainFilter = "ALL"; state.currentStep = live ? 3 : 2; renderStepper();
  $("#warnings").hidden = true;
  $("#case-id").textContent = live ? "MENGAMBIL TRANSAKSI MAINNET…" : "MENGHUBUNGKAN BUKTI…";
  for (const metric of ["loss", "traced", "hops", "recovery", "confirmed"]) $(`#metric-${metric}`).textContent = "—";
  $("#metric-crypto").textContent = "Menunggu bukti penelusuran";
  $("#metric-recovery-note").textContent = live ? "Tidak dihitung dari transaksi publik" : "Estimasi skenario sintetis";
  $("#metric-confirmed-note").textContent = "Menunggu bukti dan keputusan institusi";
  $("#graph-loading strong").textContent = live ? "Mengambil transaksi multichain asli" : "Menyusun jejak aliran dana";
  $("#graph-loading span").textContent = live ? "Hop 1–3: scanner utama · hop 4–6: Crypto APIs · permintaan dibatasi anggaran API…" : "Mencocokkan handoff FNA dengan Mirror DB dan on-chain…";
  state.logs = []; $("#activity-log").replaceChildren();
  renderInspector(); renderEvidence(); renderPakd(); renderActions();
  $("#graph-loading").hidden = false;
  $("#run-scenario-button").disabled = true;
  $("#trace-button").disabled = true;
  $("#live-trace-button").disabled = true;
  return requestId;
}

function acceptGraph(graph) {
  state.graph = graph;
  if ((graph.chain_dispersals || []).length > 1) state.focalId = graph.nodes.find(node=>node.node_type === "PAKD_USER_ACCOUNT")?.id || null;
  state.currentStep = 3;
  renderCaseSummary(); renderStepper();
  renderMetrics(); renderGraph(); resetView(); renderEvidence(); renderPakd(); renderActions();
  state.initialFocalId = state.focalId;
  renderInspector();
  const live = graph.simulation === false;
  log(`${graph.nodes.length} simpul dan ${graph.edges.length} relasi terhubung. ${live ? "Data on-chain mainnet asli; atribusi dana laporan belum terbukti." : "Sumber: snapshot FNA, REST API PAKD, dan on-chain sintetis."}`);
  if (graph.p2p_detections?.length) log(`${graph.p2p_detections.length} kecocokan escrow P2P ditemukan pada fixture.`);
  const warnings = graph.warnings || [];
  $("#warnings").textContent = warnings.join(" · ");
  $("#warnings").hidden = !warnings.length;
}

function failTrace(error, live = false) {
  toast(error.message, true);
  $("#case-id").textContent = "PENELUSURAN BELUM TERSEDIA";
  $("#executive-title").textContent = live ? "Pemindaian live belum tersedia" : "Penelusuran belum tersedia";
  $("#executive-narrative").textContent = error.message;
  $("#warnings").textContent = `Penelusuran gagal: ${error.message} ${live ? "Tidak ada transaksi demo yang disisipkan sebagai hasil live." : "Tidak ada bukti atau tiket rekomendasi aktif untuk permintaan ini."}`;
  $("#warnings").hidden = false;
  log(`Penelusuran gagal: ${error.message}`);
}

function finishTrace(requestId) {
  if (requestId !== state.request) return;
  $("#graph-loading").hidden = true;
  $("#run-scenario-button").disabled = false;
  $("#trace-button").disabled = false;
  $("#live-trace-button").disabled = false;
}

async function trace() {
  const form = $("#report-form");
  if (!form.reportValidity()) return;
  const requestId = beginTrace();
  const report = Object.fromEntries(new FormData(form));
  report.scenario_id = state.scenarioId;
  report.fiat_transfer_timestamp = `${report.fiat_transfer_timestamp}+07:00`;
  try {
    const graph = await api("/api/v1/trail/trace-report", { method: "POST", body: JSON.stringify(report) });
    if (requestId === state.request) acceptGraph(graph);
  } catch (error) { if (requestId === state.request) failTrace(error); }
  finally { finishTrace(requestId); }
}

async function traceLive() {
  const form = $("#live-form");
  $("#live-wallet").value = $("#live-wallet").value.trim();
  validateLiveAddress();
  if (!form.reportValidity()) return;
  const payload = { wallet_address: $("#live-wallet").value, max_hops: Number($("#live-hops").value), token_filter: $("#live-token").value, direction: "OUTBOUND", simulate_fna_and_mirror_origin: $("#live-origin").checked, source_chain: $("#live-chain").value, auto_scan_cross_chain: $("#live-cross-chain").checked };
  const key = $("#live-api-key").value.trim();
  if (key && !["BTC", "SOLANA"].includes(payload.source_chain)) payload.etherscan_api_key = key;
  $("#live-api-key").value = "";
  const requestId = beginTrace(true);
  $("#live-status").textContent = `${liveScanScope({ source_chain: payload.source_chain, max_hops: payload.max_hops })}. Mengambil transaksi mainnet…`;
  try {
    const graph = await api("/api/v1/trail/live-scanner", { method: "POST", body: JSON.stringify(payload) });
    if (requestId !== state.request) return;
    acceptGraph(graph);
    const provider = scannerLabel(graph.live_metadata?.provider || "Scanner multichain");
    const activity = graph.live_metadata?.activity || [];
    const count = graph.edges.filter(isLiveTransfer).length;
    if (payload.source_chain === "SOLANA") {
      $("#live-status").textContent = `${provider}: ${activity.length} aktivitas tercatat, ${count} transfer SOL native terurai. Aktivitas token/instruksi lain dapat dilihat melalui signature di detail akun.`;
    } else {
      $("#live-status").textContent = payload.source_chain === "BTC" ? `${provider}: ${count} relasi UTXO diamati. Output merupakan kemungkinan aliran; change dan pemiliknya belum diketahui.` : `${provider}: ${count} transfer asli ditampilkan. Kandidat pivot belum membuktikan perpindahan dana lintas chain.`;
    }
    $("#live-status").textContent += ` ${liveScanScope({ ...graph.live_metadata, source_chain: payload.source_chain, max_hops: payload.max_hops })}.`;
    if (graph.live_metadata?.continuation_status === "NO_FRONTIER") $("#live-status").textContent += " Fase awal belum menemukan wallet baru pada hop ketiga; Crypto APIs belum dipanggil.";
    if (graph.live_metadata?.fallback_reason === "SOLSCAN_PLAN_REQUIRED") $("#live-status").textContent = "Paket Solscan belum mengizinkan endpoint akun; fase awal diambil melalui RPC publik. " + $("#live-status").textContent;
    if (graph.live_metadata?.partial) {
      const metadata = graph.live_metadata;
      const reasons = { RATE_LIMIT: "Batas laju Crypto APIs tercapai.", CREDIT_LIMIT: "Kredit Crypto APIs tidak mencukupi.", ACCESS_LIMIT: "Paket Crypto APIs belum mengizinkan endpoint ini.", BUDGET_LIMIT: "Anggaran kelanjutan Crypto APIs tercapai.", TIMEOUT: "Waktu kelanjutan Crypto APIs habis." };
      const notice = `Hasil parsial: ${metadata.wallet_hops_traced ?? graph.hop_count ?? 0} dari ${payload.max_hops} hop wallet ditelusuri. ${metadata.cryptoapis_stop_reason || reasons[metadata.continuation_status] || "Kelanjutan Crypto APIs belum selesai."} Data yang berhasil diperoleh tetap ditampilkan.${metadata.retry_after_seconds > 0 ? ` Coba lagi setelah ${metadata.retry_after_seconds} detik.` : ""}`;
      $("#live-status").textContent += ` ${notice}`;
      $("#warnings").textContent = [notice, ...(graph.warnings || [])].join(" · ");
      $("#warnings").hidden = false;
    }
  } catch (error) {
    if (requestId !== state.request) return;
    failTrace(error, true);
    $("#live-status").textContent = `Live belum tersedia: ${error.message}`;
  } finally { finishTrace(requestId); }
}

function isLiveRecord(record) {
  const source = String(record?.metadata?.evidence_source || "").toUpperCase();
  return record?.metadata?.simulation === false || record?.metadata?.is_live === true || (/CRYPTOAPIS|ETHERSCAN|BLOCKSCOUT|MAINNET|MEMPOOL|BLOCKSTREAM|SOLANA_RPC|SOLSCAN/.test(source) && !/SIMULAT|FIXTURE|SEEDED/.test(source));
}

const chains = {
  BTC: { badge: "BTC", color: "#f5a14d", host: "mempool.space", family: "BITCOIN", icon: "/static/icons/web3icons/bitcoin.svg" },
  SOLANA: { badge: "SOL", color: "#b094ff", host: "solscan.io", family: "SOLANA", icon: "/static/icons/web3icons/solana.svg" },
  ETHEREUM: { badge: "ETH", color: "#619cf8", host: "etherscan.io", id: 1, icon: "/static/icons/web3icons/ethereum.svg" },
  BSC: { badge: "BSC", color: "#efcf65", host: "bscscan.com", id: 56, icon: "/static/icons/web3icons/binance-smart-chain.svg" },
  POLYGON: { badge: "POLY", color: "#bc8cff", host: "polygonscan.com", id: 137, icon: "/static/icons/web3icons/polygon.svg" },
  ARBITRUM: { badge: "ARB", color: "#6eb9f6", host: "arbiscan.io", id: 42161, icon: "/static/icons/web3icons/arbitrum-one.svg" },
  OPTIMISM: { badge: "OP", color: "#f38193", host: "optimistic.etherscan.io", id: 10, icon: "/static/icons/web3icons/optimism.svg" },
  BASE: { badge: "BASE", color: "#6b99ff", host: "basescan.org", id: 8453, icon: "/static/icons/web3icons/base.svg" },
  TRON: { badge: "TRON", color: "#f39185", host: "tronscan.org", id: null, icon: "/static/icons/web3icons/tron.svg" },
};
function chainName(value) {
  return ({ BITCOIN: "BTC", SOL: "SOLANA", ETH: "ETHEREUM", ETHEREUM_ERC20: "ETHEREUM", BSC_BEP20: "BSC", TRON_TRC20: "TRON" })[String(value || "").toUpperCase()] || String(value || "ETHEREUM").toUpperCase();
}
function chainInfo(record) { return chains[chainName(typeof record === "string" ? record : record?.chain || record?.metadata?.chain)] || null; }
function chainIcon(record) {
  const source = chainInfo(record)?.icon;
  if (!source) return null;
  const icon = el("img", "chain-icon");
  icon.src = source; icon.alt = ""; icon.width = 20; icon.height = 20;
  icon.setAttribute("aria-hidden", "true");
  return icon;
}
function isCandidateEdge(record) {
  return /CANDIDATE|UNVERIFIED/.test(String(record?.metadata?.correlation_status || "")) || record?.metadata?.is_candidate === true || /CANDIDATE/.test(record?.edge_type || "") || (record?.edge_type === "CROSS_CHAIN_BRIDGE" && isLiveRecord(record) && record?.metadata?.bridge_link_verified !== true);
}
function isBridgeEdge(record) { return /CROSS_CHAIN|BRIDGE_PIVOT/.test(record?.edge_type || ""); }
function scannerLabel(value) {
  const labels = { CRYPTOAPIS: "Crypto APIs", MEMPOOL_SPACE: "Mempool.space", BLOCKSTREAM_ESPLORA: "Blockstream Esplora", SOLANA_MAINNET_RPC: "Solana Mainnet RPC", SOLSCAN_PRO_API: "Solscan Pro API v2", ETHERSCAN: "Etherscan", ETHERSCAN_V2: "Etherscan V2", BLOCKSCOUT: "Blockscout", SCENARIO_FIXTURE: "Skenario sintetis" };
  return String(value || "").split(",").map((name) => labels[name.trim()] || friendly(name.trim())).join(" · ");
}
function liveScanScope(metadata = {}) {
  const maxHops = metadata.hop_strategy?.max_hops ?? metadata.max_hops ?? 6;
  const primary = metadata.source_chain === "BTC" ? "Mempool.space / Blockstream" : metadata.source_chain === "SOLANA" ? "Solscan / Solana RPC" : "Etherscan / Blockscout";
  const days = metadata.cryptoapis_history_window_days ?? (metadata.source_chain === "SOLANA" ? 1 : 14);
  const continuation = maxHops > 3 ? `; hop 4–${maxHops}: Crypto APIs (sampel terbaru hingga ${days} hari)` : "";
  const requests = metadata.requests_made === undefined ? "dibatasi anggaran API" : `${metadata.requests_made} permintaan API total`;
  const freeBudget = maxHops > 3 ? ` · Free: sampel ${metadata.cryptoapis_page_size ?? 1} transaksi/kategori/alamat; ${metadata.cryptoapis_requests_made ?? 0}/${metadata.cryptoapis_request_limit ?? 6} permintaan Crypto APIs; kredit ${metadata.cryptoapis_credits_consumed ?? 0} terukur + ${metadata.cryptoapis_credits_estimated ?? 0} perkiraan / ${metadata.cryptoapis_credit_limit ?? 2000} anggaran${metadata.cryptoapis_credits_available == null ? "" : `, sisa akun teramati ${metadata.cryptoapis_credits_available}`}` : "";
  return `${maxHops} hop diminta · hop 1–${Math.min(maxHops, 3)}: ${primary}${continuation} · ${requests}${freeBudget}`;
}
function isLiveTransfer(record) {
  if (!isLiveRecord(record) || isCandidateEdge(record) || isBridgeEdge(record)) return false;
  const chain = chainName(record.chain || record.metadata?.chain || record.chain_branch || record.metadata?.chain_branch);
  return chain === "BTC" ? /^[a-fA-F0-9]{64}$/.test(record.tx_hash || "") : chain === "SOLANA" ? /^[1-9A-HJ-NP-Za-km-z]{80,90}$/.test(record.tx_hash || "") : /^0x[a-fA-F0-9]{64}$/.test(record.tx_hash || "");
}
function explorerLink(kind, value, label, record = {}) {
  const name = chainName(record.chain || record.metadata?.chain), config = chains[name];
  if (!config) return null;
  const pattern = name === "BTC" ? kind === "address" ? /^(?:bc1[a-zA-HJ-NP-Z0-9]{11,87}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$/i : /^[a-fA-F0-9]{64}$/ : name === "SOLANA" ? kind === "address" ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/ : /^[1-9A-HJ-NP-Za-km-z]{80,90}$/ : name === "TRON" ? kind === "address" ? /^T[A-Za-z0-9]{20,99}$/ : /^(?:0x)?[a-fA-F0-9]{64}$/ : kind === "address" ? /^0x[a-fA-F0-9]{40}$/ : /^0x[a-fA-F0-9]{64}$/;
  if (!pattern.test(value || "")) return null;
  const expected = name === "TRON" ? `https://${config.host}/#/${kind === "tx" ? "transaction" : "address"}/${value}` : `https://${config.host}/${name === "SOLANA" && kind === "address" ? "account" : kind}/${value}`;
  if (record.explorer_url) {
    try {
      const supplied = new URL(record.explorer_url);
      const same = name === "SOLANA" ? supplied.href === expected : supplied.href.toLowerCase() === expected.toLowerCase();
      if (!same || supplied.username || supplied.password) return null;
    } catch { return null; }
  }
  const link = el("a", "explorer-link", label || `↗ Lihat ${kind === "address" ? "alamat" : "transaksi"} di ${config.host}`);
  link.href = expected; link.target = "_blank"; link.rel = "noopener noreferrer";
  return link;
}

const livePresets = {
  BTC: { address: "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", label: "Bitcoin", note: "Contoh alamat Bitcoin publik. UTXO output dan change tidak membuktikan kepemilikan atau atribusi dana laporan." },
  SOLANA: { address: "So11111111111111111111111111111111111111112", label: "Solana", note: "Contoh akun publik mint Wrapped SOL, bukan wallet seseorang. Signature dapat memuat instruksi token yang tidak menjadi edge transfer SOL native." },
  ETHEREUM: { address: "0xf977814e90da44bfa03b6295a0616a897441acec", label: "Ethereum", note: "Contoh alamat publik Ethereum. Aktivitas transaksi bukan bukti bahwa dana berasal dari laporan IASC." },
};
function configureLiveChain(usePreset = true) {
  const chain = $("#live-chain").value, native = ["BTC", "SOLANA"].includes(chain);
  const preset = livePresets[chain] || { ...livePresets.ETHEREUM, label: chain, note: "Contoh alamat EVM publik. Ketersediaan aktivitas dan akses scanner berbeda untuk setiap chain." };
  if (usePreset) $("#live-wallet").value = preset.address;
  $("#live-wallet").placeholder = chain === "BTC" ? "bc1… / 1… / 3…" : chain === "SOLANA" ? "Alamat Base58 Solana…" : "0x… (40 karakter heksadesimal)";
  $("#live-preset-button").textContent = `Gunakan contoh ${preset.label}`;
  $("#live-address-note").textContent = preset.note;
  $("#api-key-option").hidden = native;
  $("#api-key-note").hidden = native;
  $("#cross-chain-option").hidden = native;
  $("#live-cross-chain").disabled = native;
  if (native) $("#live-cross-chain").checked = false;
  $("#live-token").disabled = native;
  if (native) $("#live-token").value = "ALL";
  $("#live-wallet").setCustomValidity("");
}
function validateLiveAddress() {
  const chain = $("#live-chain").value, address = $("#live-wallet").value.trim();
  const valid = chain === "BTC" ? /^(?:bc1[a-zA-HJ-NP-Z0-9]{11,87}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$/i.test(address) : chain === "SOLANA" ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(address) : /^0x[a-fA-F0-9]{40}$/.test(address);
  $("#live-wallet").setCustomValidity(valid ? "" : `Format alamat ${chain} belum sesuai. Gunakan alamat mainnet untuk chain yang dipilih.`);
  return valid;
}

function renderStepper() {
  const liveOnly = state.graph?.simulation === false && !state.graph.nodes.some((node) => node.node_zone === "FIAT_BANK");
  for (const item of document.querySelectorAll(".investigation-stepper li")) {
    const step = Number(item.dataset.step), skipped = liveOnly && step < 3;
    item.classList.toggle("active", step === state.currentStep);
    item.classList.toggle("skipped", skipped);
    item.setAttribute("aria-current", step === state.currentStep ? "step" : "false");
    item.querySelector("small").textContent = skipped ? "Dilewati · mulai dari alamat publik" : ["FNA’s Money Trails", "Temukan sebaran chain", "BTC · Solana · Ethereum", "Diputuskan PUJK / PAKD"][step - 1];
  }
}
function caseChains() {
  const found = new Set((state.graph?.chain_dispersals || []).map((item) => chainName(item.chain)));
  for (const node of state.graph?.nodes || []) if (node.node_zone === "ONCHAIN_WALLET" && (node.chain || node.metadata?.chain)) found.add(chainName(node.chain || node.metadata.chain));
  const order = (chain) => { const index = ["BTC", "SOLANA", "ETHEREUM"].indexOf(chain); return index < 0 ? 100 : index; };
  return [...found].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
}
function setChainFilter(chain, nodeId) {
  if (!state.graph) return;
  stopAnimation(); state.chainFilter = chain;
  renderCaseSummary(); renderGraph(); fitGraph();
  const visibleIds = new Set(state.viewNodes.map((node) => node.id));
  if (nodeId && visibleIds.has(nodeId)) selectNode(nodeId);
  else if (state.selected && !visibleIds.has(state.selected)) selectNode(state.viewNodes.find((node) => node.node_zone === "ONCHAIN_WALLET")?.id || state.viewNodes[0]?.id);
}
function renderCaseSummary() {
  const graph = state.graph;
  if (!graph) return;
  const dispersals = graph.chain_dispersals || [], available = caseChains(), live = graph.simulation === false;
  const handoff = graph.fna_handoff || {};
  const pakd = graph.nodes.find((node) => node.node_type === "PAKD_USER_ACCOUNT")?.entity_label || "PAKD";
  $("#executive-title").textContent = live ? `Aktivitas publik ${available.join(" · ") || "blockchain"}` : dispersals.length ? `${dispersals.length} withdrawal, ${new Set(dispersals.map((row) => chainName(row.chain))).size} jalur blockchain` : "Ikuti alur perkara dan titik tindak lanjut";
  if (live) {
    const activity = graph.live_metadata?.activity || [];
    $("#executive-narrative").textContent = `${scannerLabel(graph.live_metadata?.provider || "Scanner multichain")} mengamati ${graph.nodes.filter((node) => node.node_zone === "ONCHAIN_WALLET").length} simpul on-chain dan ${graph.edges.filter(isLiveTransfer).length} relasi transaksi publik${activity.length ? `, dari ${activity.length} item aktivitas` : ""}; ${liveScanScope(graph.live_metadata)}. Atribusi terhadap dana laporan dan kepemilikan alamat belum terbukti.${available.includes("BTC") ? " Jalur BTC menampilkan kemungkinan output UTXO; change dan pemiliknya belum diketahui." : ""}`;
    if (graph.live_metadata?.fallback_reason === "SOLSCAN_PLAN_REQUIRED") $("#executive-narrative").textContent = "Solscan menolak akses endpoint karena level paket API. Fase awal memakai Solana Mainnet RPC publik. " + $("#executive-narrative").textContent;
  } else {
    const route = [handoff.victim_pujk_name, handoff.last_fna_pujk_name, pakd].filter((value, index, list) => value && list.indexOf(value) === index).join(" → ");
    const split = dispersals.map((row) => `${chainInfo(row.chain)?.badge || row.chain} ${idr(row.fiat_value_idr)} (${number(row.crypto_amount, 6)} ${row.token_symbol})`).join("; ");
    const withdrawn = dispersals.reduce((sum, row) => sum + Number(row.fiat_value_idr), 0);
    $("#executive-narrative").textContent = `Laporan ${idr(graph.total_scam_idr)} mengikuti jalur ${route || "FNA → PAKD"}.${dispersals.length ? ` Mirror API menemukan withdrawal senilai ${idr(withdrawn)}: ${split}.` : " Pencocokan hanya diteruskan jika bukti PAKD tersedia."} Pilih chain untuk mengikuti jalurnya, lalu tinjau rekomendasi pada titik akhir. Semua data perkara ini sintetis.`;
  }
  const cards = dispersals.length ? dispersals.map((row) => ({ ...row, canonical: chainName(row.chain) })) : available.map((chain) => ({ chain, canonical: chain, scanner_provider: graph.live_metadata?.provider || (live ? "Scanner multichain" : "SCENARIO_FIXTURE") }));
  $("#chain-dispersal-cards").replaceChildren(...cards.map((row) => {
    const chain = row.canonical, info = chainInfo(chain), button = el("button", `chain-card${state.chainFilter === chain ? " active" : ""}`);
    button.type = "button"; button.style.setProperty("--chain-color", info?.color || "#ad93ff"); button.dataset.chain = chain; button.setAttribute("aria-pressed", String(state.chainFilter === chain));
    const title = el("span", "chain-card-title");
    const icon = chainIcon(chain);
    if (icon) title.append(icon);
    title.append(el("span", "", `${info?.badge || chain} · ${chain === "BTC" ? "Bitcoin" : chain === "SOLANA" ? "Solana" : friendly(chain)}`));
    button.append(title);
    button.append(el("strong", "", row.fiat_value_idr !== undefined ? idr(row.fiat_value_idr) : `${graph.nodes.filter((node) => node.node_zone === "ONCHAIN_WALLET" && chainName(node.chain || node.metadata?.chain) === chain).length} simpul publik`));
    button.append(el("span", "chain-card-amount", row.crypto_amount !== undefined ? `${number(row.crypto_amount, 6)} ${row.token_symbol}` : scannerLabel(row.scanner_provider)));
    if (row.crypto_amount !== undefined) button.append(el("span", "chain-card-scanner", `Scanner: ${scannerLabel(row.scanner_provider || "SCENARIO_FIXTURE")}`));
    button.append(el("small", "", row.initial_wallet_address ? `Awal: ${short(row.initial_wallet_address, 22)}` : "Sampel aktivitas terbaru"));
    button.append(el("span", "chain-card-status", row.endpoint_status ? friendly(row.endpoint_status) : "Atribusi belum terbukti"), el("span", "chain-card-cta", `Lihat jalur ${info?.badge || chain} →`));
    button.addEventListener("click", () => setChainFilter(chain, row.initial_node_id));
    return button;
  }));
  const filters = ["ALL", "BTC", "SOLANA", "ETHEREUM", ...available.filter((chain) => !["BTC", "SOLANA", "ETHEREUM"].includes(chain))];
  $("#chain-filters").replaceChildren(el("span", "filter-caption", "LIHAT JALUR"), ...filters.map((chain) => {
    const button = el("button", state.chainFilter === chain ? "active" : "");
    const icon = chain === "ALL" ? null : chainIcon(chain);
    if (icon) button.append(icon);
    button.append(el("span", "", chain === "ALL" ? "Semua Chain" : chain));
    button.type = "button"; button.dataset.chain = chain; button.disabled = chain !== "ALL" && !available.includes(chain); button.setAttribute("aria-pressed", String(state.chainFilter === chain));
    button.addEventListener("click", () => setChainFilter(chain)); return button;
  }));
}

function renderMetrics() {
  const graph = state.graph;
  const live = graph.simulation === false;
  const scenario = state.scenarios.find((item) => item.id === graph.scenario_id);
  if ($("#case-title")) $("#case-title").textContent = live ? `Alur dana · ${chainInfo(graph.live_metadata?.source_chain)?.badge || "Wallet publik"}` : `Alur dana · ${scenario?.title || "Handoff manual"}`;
  if ($("#case-description")) $("#case-description").textContent = live ? `${graph.nodes.length} simpul dan ${graph.edges.filter(isLiveTransfer).length} relasi publik pada sampel terbaru. Kepemilikan dan hubungan dengan dana laporan belum terverifikasi.` : scenario?.description || "Handoff FNA dan bukti PAKD pada perkara aktif. Data skenario serta keputusan institusi bersifat sintetis.";
  $("#source-tag").textContent = live ? "LIVE · MULTICHAIN" : "LAB · SIMULASI";
  $("#source-tag").classList.toggle("is-live", live);
  $("#case-id").textContent = live ? `LIVE MAINNET · ${short(graph.trace_id, 15)}` : `${graph.iasc_ticket_id} · ${graph.fna_trail_id}`;
  $("#metric-loss-label").textContent = live ? "SIMPUL ON-CHAIN" : "TOTAL KERUGIAN";
  $("#metric-loss").textContent = live ? String(graph.nodes.filter((node) => node.node_zone === "ONCHAIN_WALLET").length) : idr(graph.total_scam_idr);
  $("#metric-loss-note").textContent = live ? "Alamat / transaksi pada sampel mainnet" : "Nilai laporan korban";
  $("#metric-traced-label").textContent = live ? (graph.live_metadata?.source_chain === "BTC" ? "RELASI UTXO" : "TRANSFER MAINNET") : "DANA TERLACAK";
  $("#metric-traced").textContent = live ? String(graph.edges.filter(isLiveTransfer).length) : idr(graph.total_traced_idr);
  $("#metric-crypto").textContent = live ? `${scannerLabel(graph.live_metadata?.provider || "Scanner multichain")} · sampel terbatas` : `${(graph.chain_dispersals || []).length} withdrawal · ${(new Set((graph.chain_dispersals || []).map((row) => row.chain))).size} chain · sintetis`;
  $("#metric-hops").replaceChildren(document.createTextNode(`${(live ? graph.live_metadata?.wallet_hops_traced : undefined) ?? graph.hop_count ?? graph.edges.length} `), el("em", "", "hop"));
  const pct = Number(graph.total_scam_idr) > 0 ? Math.min(100, Number(graph.recoverable_amount_idr) / Number(graph.total_scam_idr) * 100) : 0;
  $("#metric-recovery").textContent = live ? "—" : `${number(pct, 1)}%`;
  $("#metric-recovery-note").textContent = live ? "Tidak dapat disimpulkan dari data publik" : `${idr(graph.recoverable_amount_idr)} · estimasi demo`;
  $("#metric-confirmed").textContent = live ? "—" : idr(graph.confirmed_frozen_amount_idr);
  const decisions = graph.governance_requests || [];
  const approved = decisions.filter((request) => Number(request.approved_frozen_amount_idr) > 0).length;
  const pending = decisions.filter((request) => request.request_status === "SENT_WAITING_DECISION").length;
  $("#metric-confirmed-note").textContent = live ? "Tidak ada freeze otomatis dari hasil live" : `${approved} keputusan positif · ${pending} menunggu · simulasi`;
  $("#route-badges").replaceChildren(...(graph.route_badges || []).map((badge) => el("span", "route-badge", friendly(badge))));
}

function timeFilteredEdges(graph, days = "ALL", now = Date.now()) {
  if (!graph) return [];
  if (!["7", "30", "90"].includes(String(days))) return graph.edges;
  const timestamps = graph.edges.map((edge) => Date.parse(edge.timestamp)).filter(Number.isFinite);
  const reference = graph.simulation === true ? Math.max(...timestamps) : now;
  const start = reference - Number(days) * 86400000;
  return graph.edges.filter((edge) => { const timestamp = Date.parse(edge.timestamp); return Number.isFinite(timestamp) && timestamp >= start && timestamp <= reference; });
}

function transactionRows(graph, chain = "ALL", query = "", days = "ALL", now = Date.now()) {
  if (!graph) return [];
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const branches = graphBranches(graph);
  const search = query.trim().toLowerCase();
  return timeFilteredEdges(graph, days, now).filter((edge) => {
    const source = byId.get(edge.source) || {}, target = byId.get(edge.target) || {};
    const edgeChain = chainName(edge.chain || edge.metadata?.chain || branches.get(edge.source) || branches.get(edge.target) || source.chain || target.chain);
    if (chain !== "ALL" && edgeChain && edgeChain !== chain) return false;
    return !search || [edge.tx_hash, edge.label, edge.asset, edge.edge_type, source.address, source.account, source.label,
      target.address, target.account, target.label].some((value) => String(value || "").toLowerCase().includes(search));
  });
}

function renderTransactions() {
  const body = $("#transaction-body");
  if (!body) return;
  const matches = transactionRows(state.graph, state.chainFilter, $("#transaction-search")?.value || "", state.timeRange);
  const nodes = new Map((state.graph?.nodes || []).map((node) => [node.id, node]));
  body.replaceChildren(...matches.slice(0, 50).map((edge) => {
    const row = el("tr"), transaction = el("td"), from = nodes.get(edge.source), to = nodes.get(edge.target);
    const link = isLiveRecord(edge) && edge.tx_hash ? explorerLink("tx", edge.tx_hash, short(edge.tx_hash, 20), edge) : null;
    transaction.append(link || el("strong", "", short(edge.label || friendly(edge.edge_type), 32)));
    if (edge.timestamp) transaction.append(el("small", "transaction-time", dateTime(edge.timestamp)));
    const source = el("td", "transaction-address", short(from?.address || from?.account || from?.label || edge.source, 23));
    const target = el("td", "transaction-address", short(to?.address || to?.account || to?.label || edge.target, 23));
    source.title = from?.address || from?.account || from?.label || edge.source;
    target.title = to?.address || to?.account || to?.label || edge.target;
    const amount = edge.asset && edge.crypto_amount != null ? `${number(edge.crypto_amount, 8)} ${edge.asset}` : edge.amount_idr != null ? idr(edge.amount_idr) : "—";
    const evidence = el("td"), live = isLiveRecord(edge);
    evidence.append(el("span", `table-evidence ${live ? "live" : "synthetic"}`, isCandidateEdge(edge) ? "Kandidat korelasi" : live ? "Mainnet" : "Sintetis"));
    const action = el("td"), button = el("button", "transaction-detail", "Lihat ↗");
    button.type = "button"; button.setAttribute("aria-label", `Inspeksi transaksi ${edge.label || edge.tx_hash || edge.id}`);
    button.addEventListener("click", () => { selectEdge(edge.id); $(".right-panel")?.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    action.append(button); row.append(transaction, source, target, el("td", "transaction-amount", amount), evidence, action);
    return row;
  }));
  if ($("#transaction-count")) $("#transaction-count").textContent = `${Math.min(matches.length, 50)} dari ${matches.length} relasi${matches.length > 50 ? " · maks. 50 baris" : ""}`;
  if ($("#transaction-empty")) { $("#transaction-empty").textContent = state.graph ? "Tidak ada transaksi yang cocok dengan pencarian, waktu atau filter chain." : "Transaksi akan tampil setelah penelusuran."; $("#transaction-empty").hidden = matches.length > 0; }
  if ($("#time-range-note")) $("#time-range-note").textContent = state.timeRange === "ALL" ? "Semua waktu pada sampel yang sudah dimuat; bukan seluruh riwayat blockchain." : `${state.timeRange} hari ${state.graph?.simulation === true ? "relatif transaksi terakhir pada fixture" : "terakhir dari waktu saat ini"}; hanya memfilter sampel yang sudah dimuat. Transaksi tanpa waktu tidak ditampilkan.`;
}

function riskAssessment(graph, node) {
  const raw = node?.taint_score_pct;
  const score = Number(raw);
  if (graph?.simulation !== true || raw == null || typeof raw === "boolean" || String(raw).trim() === "" || !Number.isFinite(score) || score < 0 || score > 100) return { score: null, tier: "Belum dinilai", level: "unknown" };
  return { score, tier: score >= 70 ? "Tinggi" : score >= 40 ? "Sedang" : "Rendah", level: score >= 70 ? "high" : score >= 40 ? "medium" : "low" };
}

function renderRiskAssessment() {
  const graph = state.graph, selected = graph?.nodes.find((node) => node.id === state.selected);
  const assessment = riskAssessment(graph, selected);
  const scores = (graph?.nodes || []).map((node) => riskAssessment(graph, node).score).filter((score) => score != null);
  const summary = riskAssessment(graph, scores.length ? { taint_score_pct: Math.max(...scores) } : null);
  if ($("#risk-summary")) { $("#risk-summary").textContent = summary.score == null ? "Belum dinilai" : `Risiko demo · ${summary.tier}`; $("#risk-summary").dataset.level = summary.level; }
  if ($("#risk-summary-value")) $("#risk-summary-value").textContent = summary.score == null ? "—" : `${number(summary.score, 1)} / 100`;
  if ($("#risk-value")) $("#risk-value").textContent = assessment.score == null ? "Belum dinilai" : `${number(assessment.score, 1)} / 100`;
  if ($("#risk-tier")) { $("#risk-tier").textContent = assessment.score == null ? "Indikator belum tersedia" : `Indikator risiko · ${assessment.tier}`; $("#risk-tier").dataset.level = assessment.level; }
  if ($("#risk-note")) $("#risk-note").textContent = assessment.score == null ? "Tidak ada skor yang tersedia untuk bukti ini. Aktivitas publik tidak menentukan risiko AML secara otomatis." : "Berdasarkan skor taint fixture; bukan penilaian risiko AML terverifikasi. Ambang warna visual: 0–39 rendah, 40–69 sedang, 70–100 tinggi.";
  if ($("#risk-meterfill")) { $("#risk-meterfill").style.width = `${assessment.score ?? 0}%`; $("#risk-meterfill").dataset.level = assessment.level; $("#risk-meterfill").parentElement?.setAttribute("aria-label", assessment.score == null ? "Skor risiko belum dinilai" : `Indikator taint demo ${number(assessment.score, 1)} dari 100`); }
}

function nodeTheme(node) {
  const type = String(node.node_type || "").toUpperCase();
  if (/VICTIM|MULE/.test(type)) return { color: "#f1cc65", dark: "#fff3cd", icon: "♙", marker: "blue" };
  if (node.node_zone === "ONCHAIN_WALLET") return { color: "#53c7d9", dark: "#e0f5f7", icon: /TRANSACTION/.test(type) ? "⇢" : /DEX|BRIDGE/.test(type) ? "⇄" : "◇", marker: "purple" };
  if (node.node_zone === "CRYPTO_EXCHANGE_BRIDGE") return { color: "#c693cf", dark: "#f3e3f4", icon: /MERCHANT|ESCROW/.test(type) ? "↔" : "▥", marker: "amber" };
  return { color: "#d795c8", dark: "#f9e4f2", icon: "▥", marker: "blue" };
}

const zoneLabels = { FIAT_BANK: "Zona 1 · FNA’s Money Trails / PUJK", CRYPTO_EXCHANGE_BRIDGE: "Zona 2 · PAKD Scoped Mirror API", ONCHAIN_WALLET: "Zona 3 · Multichain & Bridge Trails" };

function decisionForNode(node) {
  const requests = (state.graph?.governance_requests || []).filter((request) => request.target_node_id === node.id);
  return requests.find((request) => request.request_status !== "NOT_SENT") || node.institution_decision || node.metadata?.fna_institution_decision || requests[0] || null;
}

function decisionStyle(decision) {
  const status = decision?.request_status || "";
  if (status.startsWith("APPROVED")) return { label: "DISETUJUI INSTITUSI", short: "DISETUJUI", color: "#54d7a6", className: "approved", icon: "✓" };
  if (status.startsWith("PARTIAL")) return { label: "FREEZE SEBAGIAN · INSTITUSI", short: "SEBAGIAN", color: "#efb75f", className: "partial", icon: "◐" };
  if (status.startsWith("REJECTED")) return { label: decision?.metadata?.historical ? "DITOLAK PUJK · SNAPSHOT FNA" : "DITOLAK INSTITUSI", short: decision?.metadata?.historical ? "DITOLAK · FNA" : "DITOLAK", color: "#ef8585", className: "rejected", icon: "×" };
  if (status === "SENT_WAITING_DECISION") return { label: "TIKET TERKIRIM · MENUNGGU KEPUTUSAN", short: "MENUNGGU", color: "#efb75f", className: "waiting", icon: "◷" };
  return { label: "BELUM DIKIRIM", short: "BELUM DIKIRIM", color: "#8196b0", className: "not-sent", icon: "○" };
}

function visibleGraph() {
  const filtered = state.timeRange && state.timeRange !== "ALL" ? timeFilteredEdges(state.graph,state.timeRange) : state.graph.edges;
  const connected = new Set(filtered.flatMap(edge=>[edge.source,edge.target]));
  const branches = graphBranches(state.graph);
  const nodes = state.graph.nodes.filter(node=>(filtered === state.graph.edges || connected.has(node.id) || node.id===state.focalId || node.metadata?.is_seed_wallet || node.metadata?.is_initial_wallet || /SEED|INITIAL/.test(node.node_type)) && (state.chainFilter === "ALL" || !branches.get(node.id) || branches.get(node.id) === state.chainFilter));
  const ids = new Set(nodes.map(node=>node.id));
  state.branchByNode = branches;
  return {nodes,edges:filtered.filter(edge=>ids.has(edge.source)&&ids.has(edge.target))};
}
function graphBranches(graph) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const parents = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) if (parents.has(edge.target)) parents.get(edge.target).push(edge.source);
  const branches = new Map();
  function branch(nodeId, visited = new Set()) {
    if (branches.has(nodeId)) return branches.get(nodeId);
    if (visited.has(nodeId)) return null;
    visited.add(nodeId);
    const node = byId.get(nodeId);
    if (!node) return null;
    const explicit = node.chain_branch || node.metadata?.chain_branch || (node.node_zone === "ONCHAIN_WALLET" ? node.chain || node.metadata?.chain : null);
    if (explicit) { const name = chainName(explicit); branches.set(nodeId, name); return name; }
    const inherited = new Set((parents.get(nodeId) || []).map((id) => branch(id, new Set(visited))).filter(Boolean));
    const result = inherited.size === 1 ? [...inherited][0] : null;
    branches.set(nodeId, result); return result;
  }
  for (const node of graph.nodes) branch(node.id);
  return branches;
}

function layoutNodes() {
  const scoped = visibleGraph(), board = $("#graph-scroll");
  const view = ForensicGraph.visualize({ ...state.graph, ...scoped }, { focalId: state.focalId, focus: state.focusMainPath !== false, flow: state.flowMode || "ALL", group: state.groupMode || "CHAIN", range: state.timelineRange, sortTime: state.sortTime !== false });
  state.flowProjection = view; state.viewNodes = view.nodes; state.viewEdges = view.edges; state.focalId = view.focalId;
  if (state.selected && !view.nodes.some(node=>node.id===state.selected || node.cluster_members?.some(member=>member.id===state.selected))) state.selected=null;
  if (state.selectedEdge && !view.edges.some(edge=>edge.id===state.selectedEdge || graphMemberEdges(edge).some(member=>member.id===state.selectedEdge))) state.selectedEdge=null;
  state.viewportWidth = Math.max(300, board.clientWidth); state.viewportHeight = board.clientHeight || 720;
  state.layoutModel = ArkhamEngine.prepare(view, { focalId: state.focalId, width: Math.max(1000,state.viewportWidth), height: Math.max(640,state.viewportHeight), pins: state.pins || new Map(), previous: state.positions });
  state.positions = new Map(state.layoutModel.nodes.map(node => [node.id,node])); state.lanes = [];
  state.baseWidth = state.layoutModel.width; state.baseHeight = state.layoutModel.height;
  $("#swimlanes").replaceChildren();
}

function entityIcon(node) {
  if (node.cluster_members?.length) return entityIcon(node.cluster_members[0]);
  const attributed = ForensicGraph.isExchange(node) || node.node_zone === "CRYPTO_EXCHANGE_BRIDGE" || /^PAKD_/.test(node.node_type || "");
  const name = [node.metadata?.public_entity?.label,node.entity_label,node.label,node.metadata?.mirror_match?.pakd_name].filter(Boolean).join(" ").toLowerCase();
  if (attributed && /\bindodax\b/.test(name)) return "/static/icons/entities/indodax.png";
  if (attributed && /\btokocrypto\b/.test(name)) return "/static/icons/entities/tokocrypto.png";
  if (node.node_zone === "FIAT_BANK") return null;
  return chainInfo(node)?.icon || null;
}

function edgeGeometry(source, target, count = 1, width = 2) {
  return ArkhamEngine.fanPaths(source,target,count,width);
}

function fanTransactionCount(edge) {
  const transactions = new Set((edge.members || [edge]).map((member,index) => {
    const hash = String(member.tx_hash || member.signature || member.metadata?.tx_hash || "");
    const identity = /^0x[a-f0-9]+$/i.test(hash) || /^[a-f0-9]{64}$/i.test(hash) ? hash.toLowerCase() : hash;
    const rawChain=String(member.chain || member.metadata?.chain || member.chain_branch || "").toUpperCase().replace(/_(?:ERC20|TRC20)$/,"");
    const chain=({ETH:"ETHEREUM",SOL:"SOLANA",BITCOIN:"BTC"})[rawChain] || rawChain;
    return `${chain}:${identity || member.id || index}`;
  }));
  return Math.max(1,transactions.size);
}

function edgeFlowSummary(edge) {
  const count=fanTransactionCount(edge),transfers=(edge.members || [edge]).length;
  const amount=ForensicGraph.amountSummary(edge).replace(/\s*(?:\(\d+ tx\)|\d+ tx)$/,"").replace(/[·\s]+$/,"");
  return `${amount || "Relasi"} (${count} tx${transfers>count ? `, ${transfers} transfers` : ""})`;
}

function drawGraphPositions() {
  if (!state.graphEngine) return;
  for (const group of document.querySelectorAll("#nodes .node")) {
    const node = state.positions.get(group.dataset.nodeId); if (!node) continue;
    group.setAttribute("transform", `translate(${node.x} ${node.y})`);
    const lock = group.querySelector(".node-lock"); if (lock) lock.style.visibility = state.pins.has(node.id) ? "visible" : "hidden";
  }
  for (const group of document.querySelectorAll("#edges .edge-group")) {
    const edge = state.viewEdges.find(item => item.id === group.dataset.edgeId);
    const source = state.positions.get(edge.source), target = state.positions.get(edge.target);
    const paths = edgeGeometry(source,target,fanTransactionCount(edge),edge.width);
    const strands = group.querySelectorAll(".fan-strand"); strands.forEach((path,index) => path.setAttribute("d",paths[index % paths.length]));
    group.querySelector(".edge-hit").setAttribute("d",paths[Math.floor(paths.length/2)]);
  }
  if(state.autoFit)fitGraph();
}

function showGraphTooltip(event, title, detail) {
  const tooltip = $("#graph-tooltip"), bounds = $("#graph-board").getBoundingClientRect();
  tooltip.replaceChildren(el("strong","",title),el("small","",detail)); tooltip.hidden = false;
  tooltip.style.left = `${Math.max(10,Math.min(bounds.width-260,event.clientX-bounds.left+14))}px`;
  tooltip.style.top = `${Math.max(140,Math.min(bounds.height-110,event.clientY-bounds.top+14))}px`;
}

function renderGraph() {
  if (!state.graph) return;
  stopAnimation();
  state.graphEngine?.stop(); state.graphEngine = null;
  layoutNodes();
  if ($("#graph-empty")) $("#graph-empty").hidden = state.viewNodes.length > 0;
  const byId = new Map(state.layoutModel.nodes.map(node=>[node.id,node]));
  $("#edges").replaceChildren(...state.viewEdges.map(edge => {
    const source=byId.get(edge.source),target=byId.get(edge.target);
    const side = state.layoutModel.links.find(link=>link.id===edge.id)?.side === "in" ? "inflow" : state.layoutModel.links.find(link=>link.id===edge.id)?.side === "out" ? "outflow" : "neutral";
    const group=svgEl("g",{class:`edge-group edge-fan ${side}${edge.candidate ? " candidate" : ""}`,"data-edge-id":edge.id,"data-source":edge.source,"data-target":edge.target,tabindex:0,role:"button","aria-label":`${edge.candidate ? "Kandidat · " : ""}${edgeFlowSummary(edge)}. Inspeksi transaksi.`,"aria-pressed":"false"});
    const paths=edgeGeometry(source,target,fanTransactionCount(edge),edge.width);
    group.append(svgEl("path",{class:"edge-hit",d:paths[Math.floor(paths.length/2)],"stroke-width":Math.max(16,edge.width*3)}));
    for(const path of paths) group.append(svgEl("path",{class:"fan-strand",d:path,"stroke-width":paths.length>1 ? .7 : Math.max(1,edge.width*.45)}));
    group.append(svgEl("title",{},`${edgeFlowSummary(edge)}${edge.candidate ? " · KANDIDAT belum terverifikasi" : ""}`));
    group.addEventListener("click",event=>{if(!dragState.moved){event.stopPropagation();selectEdge(edge.id);}});
    group.addEventListener("keydown",event=>{if(["Enter"," "].includes(event.key)){event.preventDefault();selectEdge(edge.id);}});
    group.addEventListener("pointerenter",event=>showGraphTooltip(event,edgeFlowSummary(edge),edge.candidate ? "Kandidat korelasi; belum membuktikan perpindahan dana" : `${fanTransactionCount(edge)} transaksi unik · ${edge.members.length} transfer · klik untuk rincian asli`));
    group.addEventListener("pointerleave",()=>$("#graph-tooltip").hidden=true);
    return group;
  }));
  $("#nodes").replaceChildren(...state.layoutModel.nodes.map(model=>{
    const node=model.original, group=svgEl("g",{class:`node arkham-node${node.id===state.focalId ? " focal" : ""}${node.id===state.selected ? " selected" : ""}`,"data-node-id":node.id,tabindex:0,role:"button","aria-label":`${node.label || node.address || node.id}. ${friendly(ForensicGraph.role(node))}. Tampilkan detail.`,"aria-pressed":String(node.id===state.selected)});
    const icon=entityIcon(node),size=model.radius>34?28:24;
    const glyph=svgEl("text",{class:"node-entity-glyph",x:0,y:-2,"text-anchor":"middle","aria-hidden":"true"},node.node_zone==="FIAT_BANK"?"▥":ForensicGraph.role(node)==="bridge"?"⇄":"◇");
    glyph.style.visibility=icon?"hidden":"visible";
    group.append(svgEl("circle",{class:"node-core",r:model.radius}),glyph);
    if(icon){const image=svgEl("image",{class:"node-entity-icon",href:icon,x:-size/2,y:-size/2-8,width:size,height:size,"clip-path":"url(#web3icon-round)","aria-hidden":"true"});image.addEventListener("error",()=>{image.style.visibility="hidden";glyph.style.visibility="visible";});group.append(image);}
    const label=svgEl("text",{class:"node-label",x:0,y:19,"text-anchor":"middle"},ForensicGraph.label(node));
    if(label.textContent.length>10){label.setAttribute("textLength",String(model.radius*1.75));label.setAttribute("lengthAdjust","spacingAndGlyphs");}
    group.append(label,svgEl("text",{class:"node-lock",x:model.radius*.65,y:-model.radius*.7,"aria-hidden":"true"},"🔒"),svgEl("title",{},`${node.label || ""}\n${node.address || node.account || node.id}\nGeser untuk pin; klik dua kali untuk lepas.`));
    group.addEventListener("click",event=>{if(!dragState.moved){event.stopPropagation();selectNode(node.id);}});
    group.addEventListener("dblclick",event=>{event.preventDefault();event.stopPropagation();setNodePinned(node.id,false);});
    group.addEventListener("keydown",event=>{if(["Enter"," "].includes(event.key)){event.preventDefault();selectNode(node.id);}else if(event.key.toLowerCase()==="p"){event.preventDefault();setNodePinned(node.id,!state.pins.has(node.id));}else if(["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)){event.preventDefault();const point=state.positions.get(node.id);movePinnedNode(node.id,point.x+(event.key==="ArrowLeft"?-20:event.key==="ArrowRight"?20:0),point.y+(event.key==="ArrowUp"?-20:event.key==="ArrowDown"?20:0));}});
    group.addEventListener("pointerenter",event=>showGraphTooltip(event,node.metadata?.public_entity?.label || node.label || node.id,`${node.chain || "Fiat / PAKD"} · ${friendly(ForensicGraph.role(node))} · klik untuk inspeksi`));
    group.addEventListener("pointerleave",()=>$("#graph-tooltip").hidden=true);
    return group;
  }));
  const d3=window.d3;
  state.autoFit=true;
  state.graphEngine=ArkhamEngine.create(state.layoutModel,{d3,onTick:drawGraphPositions,requestFrame:callback=>requestAnimationFrame(callback),cancelFrame:id=>cancelAnimationFrame(id),reducedMotion:window.matchMedia("(prefers-reduced-motion: reduce)").matches,paused:state.physicsFrozen});
  state.graphEngine.tick(Math.max(2,Math.min(24,Math.floor(256/Math.max(1,state.viewNodes.length)))));
  state.autoFit = true; drawGraphPositions(); bindD3Interactions(); updateTransform(); highlightGraphSelection();
  $("#graph-node-count").textContent=String(state.viewNodes.length); $("#graph-edge-count").textContent=String(state.viewEdges.length);
  const focal=state.viewNodes.find(node=>node.id===state.focalId);
  $("#focal-pill-label").textContent=short(focal?.address || focal?.account || focal?.label || state.graph.iasc_ticket_id,38);
  $("#visualizer-status").textContent=`${d3 ? "D3 v7" : "Local physics · offline fallback"} · ${state.viewNodes.length}/${state.graph.nodes.length} entities · ${state.viewEdges.length} flows · ${state.flowProjection.unknownUsdCount} tx without USD valuation${state.physicsFrozen ? " · layout frozen" : ""}`;
  $("#flow-focus-note").textContent="Evidence remains complete in the audit and export. USD ≥ $0.10 applies only to recorded USD values; unknown values stay visible. Timeline bars count transactions.";
  renderTimeline(); renderTransactions(); renderAuditTrail();
}

function compactIdr(value) {
  const amount = Number(value || 0);
  return amount >= 1e9 ? `Rp ${number(amount / 1e9, 1)} M` : amount >= 1e6 ? `Rp ${number(amount / 1e6, 1)} jt` : idr(amount);
}

function highlightGraphSelection() {
  if (state.activeAnimationStep) return;
  const selected=state.viewNodes.find(node=>node.id===state.selected || node.cluster_members?.some(member=>member.id===state.selected))?.id;
  const connected=new Set(selected?[selected]:[]); let matching=false;
  for(const group of document.querySelectorAll(".edge-group")){
    const bundle=state.viewEdges.find(edge=>edge.id===group.dataset.edgeId);
    const related=group.dataset.edgeId===state.selectedEdge || Boolean(state.selectedEdge&&bundle&&graphMemberEdges(bundle).some(edge=>edge.id===state.selectedEdge)) || Boolean(selected&&[group.dataset.source,group.dataset.target].includes(selected));
    if(related){connected.add(group.dataset.source);connected.add(group.dataset.target);matching=true;}
    group.classList.toggle("related",related);group.classList.toggle("dimmed",Boolean((selected||state.selectedEdge)&&!related));
  }
  for(const group of document.querySelectorAll(".node")){group.classList.toggle("dimmed",Boolean((selected||matching)&&!connected.has(group.dataset.nodeId)));group.classList.toggle("selected",group.dataset.nodeId===selected);group.setAttribute("aria-pressed",String(group.dataset.nodeId===selected));}
}

function updateTransform(fromD3=false) {
  const board=$("#graph-scroll"),svg=$("#graph");
  svg.setAttribute("viewBox",`0 0 ${Math.max(300,board.clientWidth)} ${board.clientHeight || 720}`);svg.style.height="100%";svg.style.width="100%";
  if(state.usingD3&&state.zoomBehavior&&!fromD3){window.d3.select(svg).call(state.zoomBehavior.transform,window.d3.zoomIdentity.translate(state.x,state.y).scale(state.scale));return;}
  $("#viewport").setAttribute("transform",`translate(${state.x} ${state.y}) scale(${state.scale})`);$("#zoom-level").textContent=`${Math.round(state.scale*100)}%`;
}

function resetView(){fitGraph();}
function fitGraph(){
  const board=$("#graph-scroll"),points=[...state.positions.values()];if(!points.length)return;
  const left=Math.min(...points.map(point=>point.x-point.radius-15)),right=Math.max(...points.map(point=>point.x+point.radius+15)),top=Math.min(...points.map(point=>point.y-point.radius-15)),bottom=Math.max(...points.map(point=>point.y+point.radius+15));
  const drawer=$("#inspector-drawer"),boardRect=board.getBoundingClientRect(),covered=drawer.hidden?0:Math.max(0,boardRect.right-drawer.getBoundingClientRect().left);
  const banner=$("#animation-step"),bannerBottom=banner.hidden?0:banner.getBoundingClientRect().bottom-boardRect.top+12;
  const topPadding=Math.max(145,($(".visualizer-overlay")?.clientHeight || 0)+34,bannerBottom),bottomPadding=Math.max(95,($(".activity-timeline")?.clientHeight || 0)+30);
  const availableWidth=Math.max(180,board.clientWidth-100-covered),availableHeight=Math.max(80,board.clientHeight-topPadding-bottomPadding);
  const focal=state.positions.get(state.focalId)||{x:(left+right)/2,y:(top+bottom)/2};
  const halfWidth=Math.max(focal.x-left,right-focal.x,1),halfHeight=Math.max(focal.y-top,bottom-focal.y,1);
  state.scale=Math.max(.01,Math.min(1.15,availableWidth/(halfWidth*2),availableHeight/(halfHeight*2)));
  state.x=(board.clientWidth-covered)/2-focal.x*state.scale;state.y=topPadding+availableHeight/2-focal.y*state.scale;
  updateTransform();
}
function zoom(factor,point={x:$("#graph-scroll").clientWidth/2,y:$("#graph-scroll").clientHeight/2}){
  state.autoFit=false;
  const next=Math.max(.01,Math.min(4,state.scale*factor)),ratio=next/state.scale;state.x=point.x-(point.x-state.x)*ratio;state.y=point.y-(point.y-state.y)*ratio;state.scale=next;updateTransform();
}
function openInspector(open=true){$("#inspector-drawer").hidden=!open;$("#more-info-button").setAttribute("aria-expanded",String(open));if(state.autoFit)fitGraph();}
function movePinnedNode(id,x,y){state.autoFit=false;state.pins.set(id,{x,y});if(state.graphEngine)state.graphEngine.pin(id,x,y);else drawGraphPositions();}
function setNodePinned(id,pinned){
  const point=state.positions.get(id);if(!point)return;
  if(pinned)movePinnedNode(id,point.x,point.y);else{state.pins.delete(id);state.graphEngine?.unpin(id);drawGraphPositions();}
  if(state.selected===id)renderInspector();
}
function setFocalNode(id){
  state.pins.delete(id);state.focalId=id;state.selected=id;state.selectedEdge=null;state.chainFilter="ALL";stopAnimation();renderCaseSummary();renderGraph();fitGraph();selectNode(id);
}

function selectNode(id, open = true, fromAnimation = false) {
  if (!fromAnimation) stopAnimation();
  if (open) openInspector();
  state.selectedEdge = null;
  for (const group of document.querySelectorAll(".edge-group")) { group.classList.remove("selected"); group.setAttribute("aria-pressed", "false"); }
  state.selected = id;
  for (const group of document.querySelectorAll(".node")) {
    const selected = group.dataset.nodeId === id;
    group.classList.toggle("selected", selected);
    group.setAttribute("aria-pressed", String(selected));
  }
  const nodeRequests = (state.graph?.governance_requests || []).filter((request) => request.target_node_id === id);
  if (!nodeRequests.some((request) => request.request_id === state.selectedRequestId)) state.selectedRequestId = nodeRequests[0]?.request_id || null;
  highlightGraphSelection();
  renderInspector();
  renderEvidence();
  renderPakd();
  renderActions();
}

function selectEdge(id) {
  stopAnimation();
  openInspector();
  state.selectedEdge = id;
  state.selected = null;
  state.selectedRequestId = null;
  for (const group of document.querySelectorAll(".node")) { group.classList.remove("selected"); group.setAttribute("aria-pressed", "false"); }
  for (const group of document.querySelectorAll(".edge-group")) { const selected = group.dataset.edgeId === id; group.classList.toggle("selected", selected); group.setAttribute("aria-pressed", String(selected)); }
  highlightGraphSelection();
  renderInspector(); renderActions();
}

function addDetail(list, label, value, className = "") {
  if (value === undefined || value === null || value === "") return;
  const row = el("div");
  row.append(el("dt", "", label), el("dd", className, typeof value === "object" ? JSON.stringify(value) : String(value)));
  list.append(row);
}

function graphMemberEdges(edge) {
  const byId = new Map((state.graph?.edges || []).map(item => [item.id, item])), items = new Map();
  for (const member of edge.members || [edge]) {
    const ids = member.metadata?.collapsed_api_steps;
    if (ids?.length) { for (const id of ids) if (byId.has(id)) items.set(id, byId.get(id)); }
    else items.set(member.id, byId.get(member.id) || member);
  }
  return [...items.values()];
}

function nodeInflowEdges(node) {
  const targets = new Set((node.cluster_members || [node]).map(member => member.id));
  return transactionRows(state.graph, state.chainFilter || "ALL", "", state.timeRange || "ALL").filter(edge => targets.has(edge.target));
}

function auditTrailRows(graph) {
  if (!graph) return [];
  const edges = transactionRows(graph, state.chainFilter || "ALL", "", state.timeRange || "ALL");
  const view = ForensicGraph.project({ ...graph, edges }, { focus: false });
  const byId = new Map(graph.nodes.map(node => [node.id, node]));
  return edges.map(edge => ({ edge, source: byId.get(edge.source), target: byId.get(edge.target),
    hop: view.hops.get(edge.source) || 0, targetHop: view.hops.get(edge.target) || 0,
    sourceRole: view.roles.get(edge.source), attributed: ForensicGraph.isExchange(byId.get(edge.target) || {}) }))
    .sort((a,b) => Number(b.sourceRole === "source") - Number(a.sourceRole === "source") || a.hop - b.hop || String(a.edge.timestamp || "").localeCompare(String(b.edge.timestamp || "")) || String(a.edge.id).localeCompare(String(b.edge.id)));
}

function flowSummaryRows(edges) {
  const totals=new Map();
  function add(asset,chain,contract,raw){const amount=Number(raw);if(raw==null||typeof raw==="boolean"||String(raw).trim()===""||!Number.isFinite(amount)||amount<=0)return;const key=JSON.stringify([asset,chain,contract]);const current=totals.get(key);const total=(current?.amount||0)+amount;if(Number.isFinite(total))totals.set(key,{asset,chain,contract,amount:total});}
  for(const edge of edges){
    const contract=String(edge.metadata?.token_contract||edge.metadata?.contract_address||edge.contract||edge.contract_address||"");
    if(edge.asset)add(edge.asset,edge.chain||edge.metadata?.chain||"",/^0x/i.test(contract)?contract.toLowerCase():contract,edge.crypto_amount);
    add("IDR","","",edge.amount_idr);add("USD","","",edge.amount_usd??edge.usd_amount??edge.usd_value??edge.value_usd??edge.metadata?.amount_usd??edge.metadata?.usd_value??edge.metadata?.value_usd);
  }
  return [...totals.values()];
}

function renderNodeControls(node){
  const controls=el("div","inspector-actions"),pin=el("button","button secondary",state.pins.has(node.id)?"🔓 Unpin Node":"🔒 Pin Node"),focal=el("button","button","🎯 Set as Center / Expand Trail");
  pin.type="button";pin.id="node-pin-button";pin.setAttribute("aria-pressed",String(state.pins.has(node.id)));pin.disabled=!state.positions.has(node.id);
  pin.addEventListener("click",()=>setNodePinned(node.id,!state.pins.has(node.id)));
  focal.type="button";focal.id="node-focal-button";focal.title="Pivot pada bukti yang sudah dimuat; tombol Expand hop mengambil data tambahan.";
  focal.addEventListener("click",()=>setFocalNode(node.cluster_members?.[0]?.id||node.id));controls.append(pin,focal);return controls;
}
function renderFlowSummary(node){
  const ids=new Set((node.cluster_members||[node]).map(item=>item.id)),edges=transactionRows(state.graph,state.chainFilter,"",state.timeRange),section=el("section","flow-summary");
  for(const [direction,label] of [["in","Total Inflow"],["out","Total Outflow"]]){
    const part=el("div",direction),records=edges.filter(edge=>ids.has(edge[direction==="in"?"target":"source"]));
    part.append(el("span","",label));const totals=flowSummaryRows(records);
    if(!totals.length)part.append(el("strong","","—"));
    for(const row of totals){const value=el("strong","",`${number(row.amount,8)} ${row.asset}`);value.title=`${row.chain || row.asset}${row.contract ? " · "+row.contract : ""}`;part.append(value);}
    section.append(part);
  }
  return section;
}
function renderTransferItems(edges){
  const section=el("details","technical-details transfer-items");section.open=true;section.append(el("summary","",`${edges.length} transaksi / relasi · rincian asli`));
  const scroll=el("div","connected-transactions"),table=el("table"),head=el("thead"),heading=el("tr"),body=el("tbody"),byId=new Map((state.graph?.nodes||[]).map(node=>[node.id,node]));
  for(const label of ["TxHash / bukti","Timestamp","Token / nilai","Counterparty","Inspeksi"]){const th=el("th","",label);th.scope="col";heading.append(th);}head.append(heading);
  for(const edge of edges){
    const row=el("tr"),hash=el("td"),counterparty=el("td"),action=el("td"),link=isLiveRecord(edge)&&edge.tx_hash?explorerLink("tx",edge.tx_hash,short(edge.tx_hash,15),edge):null;
    hash.append(link||el("code","",short(edge.tx_hash||edge.label||edge.id,22)),el("small","",isCandidateEdge(edge)?"KANDIDAT · belum terverifikasi":isLiveRecord(edge)?"Mainnet · atribusi belum terbukti":"Bukti sintetis"));
    hash.title=edge.tx_hash||edge.id;
    const from=byId.get(edge.source),to=byId.get(edge.target);counterparty.append(el("span","",`${short(from?.metadata?.public_entity?.label||from?.address||from?.account||from?.label||edge.source,16)} → ${short(to?.metadata?.public_entity?.label||to?.address||to?.account||to?.label||edge.target,16)}`));
    const amount=edge.asset&&edge.crypto_amount!=null?`${number(edge.crypto_amount,12)} ${edge.asset}`:edge.amount_idr!=null?idr(edge.amount_idr):"—";
    const value=el("td","",amount);value.append(el("small","",edge.chain||edge.metadata?.chain||"Fiat / ledger"));
    const button=el("button","transaction-detail","Lihat");button.type="button";button.addEventListener("click",()=>selectEdge(edge.id));action.append(button);
    row.append(hash,el("td","",edge.timestamp?dateTime(edge.timestamp):"Tanpa timestamp"),value,counterparty,action);body.append(row);
  }
  table.append(head,body);scroll.append(table);section.append(scroll);return section;
}

function renderBundleInspector(bundle) {
  const inspector = $("#inspector"), members = graphMemberEdges(bundle), byId = new Map(state.viewNodes.map(node => [node.id, node]));
  const details = el("dl", "detail-list");
  addDetail(details, "Dari", byId.get(bundle.source)?.label || bundle.source);
  addDetail(details, "Ke", byId.get(bundle.target)?.label || bundle.target);
  addDetail(details, "Jumlah bukti", `${members.length} transaksi / relasi asli`);
  addDetail(details, "Ringkasan aset utama", ForensicGraph.amountSummary(bundle));
  addDetail(details, "Status hubungan", bundle.candidate ? "KANDIDAT · mencakup hubungan yang belum terverifikasi" : "Relasi pada bukti teramati; bukan bukti kepemilikan atau dana laporan");
  inspector.replaceChildren(el("div", "inspector-type", "ALUR TERAGREGASI"), el("h3", "", ForensicGraph.amountSummary(bundle)), details,
    el("p", "scope-note", "Satu panah merangkum relasi searah. Jumlah aset berbeda tidak digabung sebagai satu nilai uang. Rincian asli, termasuk langkah PAKD yang diringkas, tersedia di bawah."), renderTransferItems(members));
}

function renderClusterInspector(node) {
  const inspector = $("#inspector"), list = el("div", "cluster-list");
  for (const member of node.cluster_members) {
    const button = el("button", "audit-action", member.metadata?.public_entity?.label || member.label || member.address || member.id);
    button.type = "button"; button.append(el("small", "", member.address || member.account || member.id));
    button.addEventListener("click", () => selectNode(member.id)); list.append(button);
  }
  inspector.replaceChildren(el("div", "inspector-type", "RINGKASAN VISUAL"), el("h3", "", `${node.cluster_members.length} dompet nilai kecil lainnya`),
    el("p", "scope-note", "Pilih alamat untuk memeriksa bukti anggotanya. Kelompok hanya memakai atribusi pemilik yang eksplisit."), renderNodeControls(node), renderFlowSummary(node), list);
}

function renderAuditTrail() {
  const body = $("#audit-trail-body"); if (!body) return;
  const rows = auditTrailRows(state.graph), contents = []; let previous = null;
  for (const row of rows) {
    const label = row.sourceRole === "source" ? "Sumber / Origin" : `Hop ${row.hop}`;
    if (label !== previous) { const header = el("tr", "audit-hop-heading"), cell = el("td", "", label); cell.colSpan = 5; header.append(cell); contents.push(header); previous = label; }
    const tr = el("tr"), route = el("td"), value = el("td"), attribution = el("td"), actions = el("td", "audit-actions");
    route.append(el("strong", "", `${row.source?.metadata?.public_entity?.label || row.source?.label || row.edge.source} → ${row.target?.metadata?.public_entity?.label || row.target?.label || row.edge.target}`));
    route.append(el("small", "", `${short(row.source?.address || row.source?.account || row.edge.source,25)} → ${short(row.target?.address || row.target?.account || row.edge.target,25)}`));
    value.append(el("strong", "", row.edge.asset && row.edge.crypto_amount != null ? `${number(row.edge.crypto_amount,12)} ${row.edge.asset}` : row.edge.amount_idr != null ? idr(row.edge.amount_idr) : "—"));
    value.append(el("small", "", row.edge.chain || row.edge.metadata?.chain || "Fiat / ledger"));
    if (row.edge.timestamp) value.append(el("small", "", dateTime(row.edge.timestamp)));
    const candidate = isCandidateEdge(row.edge);
    attribution.append(el("span", `audit-entity-badge${row.attributed ? " target" : ""}`, candidate ? "Kandidat · belum terverifikasi" : row.target?.node_zone === "FIAT_BANK" ? "Rekening pada bukti" : row.attributed ? "Exchange teridentifikasi" : "Dompet belum teratribusi"));
    attribution.append(el("small", "", isLiveRecord(row.edge) ? "Label publik · pemilik belum diverifikasi" : "Skenario sintetis"));
    const inspect = el("button", "audit-action", "Inspeksi"); inspect.type = "button";
    inspect.addEventListener("click", () => { selectEdge(row.edge.id); $(".right-panel").scrollIntoView({block:"nearest",behavior:"smooth"}); }); actions.append(inspect);
    const request = (state.graph.governance_requests || []).find(item => item.target_node_id === row.edge.target);
    if (request) { const freeze = el("button", "audit-action", "Tinjau rekomendasi freeze"); freeze.type = "button";
      freeze.addEventListener("click", () => { selectNode(row.edge.target); state.selectedRequestId = request.request_id; renderActions(); $("#action-list").scrollIntoView({block:"nearest",behavior:"smooth"}); }); actions.append(freeze); }
    tr.append(el("td", "", `${row.sourceRole === "source" ? "Origin" : row.hop} → ${row.targetHop}`),route,value,attribution,actions); contents.push(tr);
  }
  body.replaceChildren(...contents); if ($("#audit-trail-empty")) $("#audit-trail-empty").hidden = rows.length > 0;
}

function setInvestigationView(view) {
  state.investigationView = view; stopAnimation();
  const audit = view === "audit";
  if (audit) state.graphEngine?.stop();
  $("#flow-graph-view").hidden = audit; $("#audit-trail-view").hidden = !audit;
  $("#flow-view-button").setAttribute("aria-pressed", String(!audit)); $("#audit-view-button").setAttribute("aria-pressed", String(audit));
  $("#fit-button").disabled = audit; $("#focus-main-path").disabled = audit;
  if (state.graph) { if (audit) { renderAuditTrail(); $("#flow-focus-note").textContent = "Audit menampilkan semua transaksi pada chain dan rentang waktu aktif, termasuk yang disembunyikan pada peta."; } else { renderGraph(); fitGraph(); } }
}

function renderInspector() {
  renderRiskAssessment();
  const inspector = $("#inspector");
  const edge = state.viewEdges.find((candidate) => candidate.id === state.selectedEdge) || state.graph?.edges.find((candidate) => candidate.id === state.selectedEdge);
  if (edge?.members) { renderBundleInspector(edge); return; }
  if (edge) {
    const details = el("dl", "detail-list");
    addDetail(details, "Jaringan", `${edge.chain || edge.metadata?.chain || "—"}${edge.chain_id ? ` [${edge.chain_id}]` : ""}`);
    addDetail(details, "Bridge protocol", edge.bridge_protocol_name);
    if (isBridgeEdge(edge)) {
      addDetail(details, "Pivot lintas chain", `${edge.bridged_from_chain || "?"} → ${edge.bridged_to_chain || "?"}`);
      addDetail(details, "Status hubungan dana", isCandidateEdge(edge) ? "KANDIDAT KORELASI · alamat/waktu berdekatan, belum membuktikan satu perpindahan dana" : "Alur bridge sintetis pada skenario demo");
    }
    addDetail(details, "Sumber data", edge.metadata?.evidence_source || "Fixture skenario sintetis");
    addDetail(details, "Dari", state.graph.nodes.find((node) => node.id === edge.source)?.address || edge.source, "mono");
    addDetail(details, "Ke", state.graph.nodes.find((node) => node.id === edge.target)?.address || edge.target, "mono");
    addDetail(details, "Nilai transfer", edge.asset && edge.crypto_amount != null ? `${number(edge.crypto_amount, 12)} ${edge.asset}` : idr(edge.amount_idr));
    addDetail(details, "Waktu · WIB", edge.timestamp ? dateTime(edge.timestamp) : null);
    addDetail(details, "TX hash", edge.tx_hash, "mono");
    if (edge.metadata?.collapsed_api_steps) addDetail(details, "Ringkasan visual", "Langkah pencocokan dan konversi PAKD diringkas. Bukti aslinya tersedia di Log API & Mirror DB.");
    if (chainName(edge.chain || edge.metadata?.chain) === "BTC") addDetail(details, "Atribusi UTXO", "Kemungkinan aliran output; change dan pemilik belum diketahui. Bukan jumlah dana laporan yang terkonfirmasi.");
    addDetail(details, "Scanner", scannerLabel(edge.scanner_provider || edge.metadata?.scanner_provider));
    addDetail(details, "Kontrak token", edge.metadata?.token_contract || edge.metadata?.contract_address, "mono");
    inspector.replaceChildren(el("div", "inspector-type", "DETAIL TRANSAKSI"), el("h3", "", edge.label || friendly(edge.edge_type)), el("p", "inspector-entity", isCandidateEdge(edge) ? "Kandidat pivot lintas chain · belum terkonfirmasi" : isLiveRecord(edge) ? "Transaksi publik multichain asli · atribusi belum terbukti" : "Bukti sintetis untuk demonstrasi"), details);
    const link = isLiveRecord(edge) ? explorerLink("tx", edge.tx_hash, undefined, edge) : null;
    if (link) inspector.append(link);
    if (isCandidateEdge(edge)) {
      for (const side of ["source", "destination"]) {
        const chain = edge[side === "source" ? "bridged_from_chain" : "bridged_to_chain"];
        const hash = edge.metadata?.[`${side}_tx_hash`];
        const evidence = explorerLink("tx",hash,`↗ Bukti ${side === "source" ? "chain asal" : "chain tujuan"}`,{chain,explorer_url:edge.metadata?.[`${side}_explorer_url`]});
        if (evidence) inspector.append(evidence);
      }
    }
    return;
  }
  const node = state.graph?.nodes.find((candidate) => candidate.id === state.selected) || state.viewNodes.find((candidate) => candidate.id === state.selected);
  if (!node) {
    const empty = el("div", "inspector-empty");
    empty.append(el("span", "", "⌖"), el("h3", "", "Inspeksi simpul"), el("p", "", "Pilih rekening, escrow, atau wallet pada grafik untuk melihat bukti."));
    inspector.replaceChildren(empty);
    return;
  }
  if (node.cluster_members) { renderClusterInspector(node); return; }
  const theme = nodeTheme(node);
  const type = el("div", "inspector-type", friendly(node.node_type || node.node_zone));
  type.style.color = theme.color;
  const heading = el("h3", "", node.label);
  const entity = el("p", "inspector-entity", node.entity_label || friendly(node.node_zone));
  const decision = decisionForNode(node);
  const decisionTheme = decisionStyle(decision);
  const status = el("span", `status-pill decision-${decisionTheme.className}`, decision ? `${decisionTheme.icon} ${decisionTheme.label}` : isLiveRecord(node) ? "● DATA PUBLIK MULTICHAIN" : "● Bukti sintetis · untuk peninjauan");
  const details = el("dl", "detail-list");
  addDetail(details, "Scanner", scannerLabel(node.scanner_provider || node.metadata?.scanner_provider));
  addDetail(details, "Rekening / akun", node.account, "mono");
  addDetail(details, "Alamat blockchain", node.address, "mono");
  addDetail(details, "Jaringan", `${node.chain || "—"}${node.chain_id ? ` [${node.chain_id}]` : ""}`);
  addDetail(details, "Bridge protocol", node.bridge_protocol_name);
  if (node.is_bridge_node) addDetail(details, "Jalur bridge", `${node.bridged_from_chain || node.chain || "?"} → ${node.bridged_to_chain || "belum diketahui"}`);
  if (node.metadata?.discovery === "SAME_ADDRESS_ACTIVITY_ONLY") addDetail(details, "Korelasi lintas chain", "Aktivitas alamat yang sama; belum ada bukti perpindahan dana/bridge antar-chain.");
  addDetail(details, "Nominal terkait", Number(node.amount_idr) > 0 ? idr(node.amount_idr) : null);
  if (node.metadata?.tx_hash) addDetail(details, "TX hash", node.metadata.tx_hash, "mono");
  addDetail(details, "Sisa saldo teramati", node.metadata?.balance_status === "NOT_FETCHED" ? "Belum diambil" : node.asset && node.balance_crypto != null ? `${number(node.balance_crypto, 4)} ${node.asset}` : null, "detail-highlight");
  addDetail(details, "Jumlah aset", node.asset && Number(node.crypto_amount) > 0 ? `${number(node.crypto_amount, 4)} ${node.asset}` : null);
  if (node.node_zone === "ONCHAIN_WALLET" && node.taint_score_pct != null) {
    addDetail(details, "Taint score · model demo", `${number(node.taint_score_pct, 1)}%`);
    const meter = el("div", "taint-meter");
    const fill = el("span"); fill.style.width = `${Math.max(0, Math.min(100, Number(node.taint_score_pct)))}%`; meter.append(fill); details.lastElementChild.append(meter);
  }
  else if (node.node_zone === "ONCHAIN_WALLET") addDetail(details, "Taint score", "Belum teratribusi");
  const incoming = state.graph.edges.filter((edge) => edge.target === node.id && edge.tx_hash);
  const technicalDetails = el("details", "technical-details"); technicalDetails.append(el("summary", "", "Detail bukti & metadata"));
  const extra = el("dl", "detail-list"); technicalDetails.append(extra);
  addDetail(extra, "TX hash masuk", incoming.map((edge) => edge.tx_hash).join("\n"), "mono");
  const metadata = node.metadata || {};
  const metadataLabels = { merchant_id: "ID merchant", scammer_uid: "UID akun terkait", uid: "UID akun", kyc_name: "Identitas KYC sintetis", owner_name: "Nama pemilik sintetis", owner: "Nama pemilik sintetis", platform_name: "Platform", platform: "Platform", heuristic_score: "Skor heuristik merchant", p2p_score: "Skor heuristik merchant", confidence: "Keyakinan kecocokan", balance_idr: "Sisa saldo rupiah", available_balance_idr: "Sisa saldo bank teramati", balance_source: "Sumber saldo", order_id: "Order escrow", data_source: "Sumber bukti", evidence_source: "Sumber bukti", note: "Catatan", notes: "Catatan" };
  for (const [key, value] of Object.entries(metadata)) if (metadataLabels[key]) addDetail(extra, metadataLabels[key], /balance_idr/.test(key) ? idr(value) : value, key === "available_balance_idr" ? "detail-highlight" : "");
  addDetail(extra, "NIK KYC · tersamarkan", metadata.kyc_nik_masked);
  addDetail(extra, "Referensi RRN", metadata.rrn_reference_number, "mono");
  addDetail(extra, "ID trail FNA", metadata.fna_trail_id, "mono");
  if (metadata.kyc) {
    addDetail(extra, "Nama KYC sintetis", metadata.kyc.name);
    addDetail(extra, "Referensi dokumen KYC", metadata.kyc.document_ref, "mono");
    addDetail(extra, "Status KYC fixture", friendly(metadata.kyc.status));
  }
  const mirror = metadata.mirror_deposit || metadata.mirror_match || metadata.mirror_lookup;
  if (mirror) {
    addDetail(extra, "Akses data PAKD", "REST API Consumer → PAKD Scoped Mirror API; SQL hanya di provider PAKD");
    addDetail(extra, "Sumber identitas", "Respons API PAKD sintetis · kecocokan teknis, bukan identitas pemilik live terverifikasi");
    addDetail(extra, "PAKD", mirror.pakd_name || mirror.pakd_code);
    addDetail(extra, "UID PAKD", mirror.user_uid || mirror.pakd_user_uid, "mono");
    addDetail(extra, "NIK tersamarkan", mirror.nik_masked || mirror.kyc_nik_masked, "mono");
    addDetail(extra, "Nama sintetis", mirror.kyc_name_masked);
    addDetail(extra, "Waktu deposit VA · WIB", (mirror.inflow_timestamp || mirror.deposit_timestamp) ? dateTime(mirror.inflow_timestamp || mirror.deposit_timestamp) : null);
    addDetail(extra, "Wallet terdaftar", mirror.initial_wallet_address || mirror.wallet_address, "mono");
    addDetail(extra, "Tipe wallet mirror", mirror.wallet_type);
  }
  if (metadata.heuristic) {
    addDetail(extra, "Skor heuristik merchant", metadata.heuristic.score ?? metadata.heuristic.score_pct);
    for (const [key, value] of Object.entries(metadata.heuristic)) if (!/score|score_pct/.test(key)) addDetail(extra, friendly(key), value);
  }
  if (!incoming.length && metadata.tx_hashes) addDetail(extra, "TX hashes", Array.isArray(metadata.tx_hashes) ? metadata.tx_hashes.join("\n") : metadata.tx_hashes, "mono");
  if (decision) {
    addDetail(extra, "Institusi pemegang keputusan", `${decision.institution_category} · ${decision.institution_name}`);
    addDetail(extra, "Konteks keputusan", decision.metadata?.historical ? "Riwayat pada snapshot FNA; terpisah dari tiket rekomendasi baru" : "Tiket governance pada trace aktif · simulasi");
    addDetail(extra, "Catatan institusi", decision.institution_decision_notes);
    addDetail(extra, "Unit pemberi keputusan", decision.decided_by_unit);
    addDetail(extra, "Nominal disetujui institusi", idr(decision.approved_frozen_amount_idr));
  }
  inspector.replaceChildren(type, heading, entity, status, renderNodeControls(node), renderFlowSummary(node), details);
  const transfers = state.graph.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  if (transfers.length) inspector.append(renderTransferItems(transfers));
  if (extra.children.length) inspector.append(technicalDetails);
  if (isLiveRecord(node)) {
    const ownTx = node.metadata?.tx_hash;
    const link = ownTx ? explorerLink("tx", ownTx, undefined, node) : explorerLink("address", node.address, undefined, node);
    if (link) inspector.append(link);
    for (const edge of incoming.filter(isLiveRecord).slice(0, 5)) {
      const txLink = explorerLink("tx", edge.tx_hash, `↗ TX ${short(edge.tx_hash, 22)}`, edge);
      if (txLink) inspector.append(txLink);
    }
    addDetail(details, "Atribusi dana laporan", "Belum terbukti · transfer publik bukan ukuran kerugian/recovery");
    if (chainName(node.chain || node.metadata?.chain) === "BTC") addDetail(details, "UTXO", "Output dan change tidak mengidentifikasi pemilik dana.");
    const activities = node.metadata?.activity || ((node.metadata?.is_seed_wallet || node.metadata?.is_initial_wallet) ? state.graph.live_metadata?.activity : []) || [];
    if (activities.length) {
      const activity = el("details", "technical-details public-activity"); activity.open = true;
      activity.append(el("summary", "", `${activities.length} signature aktivitas publik`), el("p", "scope-note", "Signature dapat memuat instruksi token atau instruksi lain yang tidak digambar sebagai transfer SOL native."));
      for (const entry of activities.slice(0, 20)) {
        const link = explorerLink("tx", entry.signature, `↗ ${short(entry.signature, 20)} · ${friendly(entry.status)}`, { chain: "SOLANA", explorer_url: entry.explorer_url });
        if (link) activity.append(link);
      }
      inspector.append(activity);
    }
  }
  if (node.node_zone === "ONCHAIN_WALLET" && node.address && state.graph.simulation !== false) {
    const expand = el("button", "button expand-button", "+ Expand hop on-chain berikutnya");
    expand.type = "button";
    expand.addEventListener("click", () => expandNode(node, expand));
    inspector.append(expand);
  }
}

function renderPakd() {
  const records = state.graph?.pakd_trails || [];
  const linked = records.filter((record) => record.target_node_id === state.selected);
  const visible = linked.length ? linked : records;
  $("#pakd-count").textContent = `${records.length} RECORD`;
  if (!visible.length) { $("#pakd-list").replaceChildren(el("p", "muted small", "Ledger PAKD hanya tampil setelah handoff cocok dengan data mitra sintetis.")); return; }
  $("#pakd-list").replaceChildren(...visible.map((record) => {
    const card = el("article", "pakd-card");
    card.append(el("strong", "pakd-name", record.pakd_name), el("span", "pakd-intake", friendly(record.intake_mechanism)));
    const details = el("dl", "detail-list");
    addDetail(details, "Akun PAKD / UID", record.pakd_user_uid, "mono");
    addDetail(details, "NIK KYC tersamarkan", record.kyc_nik_masked, "mono");
    addDetail(details, "Fiat masuk", idr(record.fiat_in_idr));
    addDetail(details, "Konversi / aset diperoleh", `${number(record.crypto_purchased_amount, 4)} ${record.crypto_purchased_token}`);
    addDetail(details, "Sisa saldo internal PAKD", idr(record.remaining_pakd_balance_idr));
    addDetail(details, "Status withdrawal", friendly(record.withdrawal_status));
    addDetail(details, "Jaringan", record.blockchain_network);
    addDetail(details, "Initial wallet tersingkap", record.initial_wallet_address, "mono");
    addDetail(details, "Withdrawal tx hash", record.withdrawal_tx_hash, "mono");
    addDetail(details, "RRN pencocokan", record.metadata?.rrn_reference_number, "mono");
    addDetail(details, "Alamat deposit on-chain", record.metadata?.deposit_address, "mono");
    card.append(details);
    return card;
  }));
}

function renderEvidence() {
  const list = $("#evidence-list");
  const detections = state.graph?.p2p_detections || [];
  $("#evidence-count").textContent = `${detections.length} MATCH`;
  if (!detections.length) { list.replaceChildren(el("p", "muted small", "Tidak ada jalur P2P terkonfirmasi pada skenario ini.")); return; }
  list.replaceChildren(...detections.map((match) => {
    const card = el("article", "evidence-card");
    const top = el("div", "evidence-top");
    const onramp = match.p2p_direction === "ON_RAMP_DEPOSIT";
    top.append(el("span", "evidence-direction", onramp ? "↗ P2P ON-RAMP" : "↙ P2P OFF-RAMP"), el("span", "evidence-platform", match.platform_name));
    const amount = el("div", "evidence-amount", number(match.crypto_amount_usdt)); amount.append(el("small", "", "USDT"));
    const details = el("dl", "detail-list");
    addDetail(details, "Order escrow", match.order_id, "mono");
    addDetail(details, "Merchant / rekening", `${match.merchant_id || "—"} · ${match.merchant_bank_account || "—"}`, "mono");
    addDetail(details, "UID akun terkait", match.scammer_uid, "mono");
    addDetail(details, "Nilai settlement", idr(match.fiat_amount_idr));
    const evidence = match.evidence || {};
    const bankTime = evidence.bank_transfer_timestamp || evidence.bank_transfer_time || evidence.bank_timestamp || evidence.transfer_timestamp;
    addDetail(details, "Transfer bank · WIB", bankTime ? dateTime(bankTime) : null);
    addDetail(details, "Escrow release · WIB", match.escrow_release_timestamp ? dateTime(match.escrow_release_timestamp) : null);
    addDetail(details, "Wallet terkait", match.linked_onchain_address, "mono");
    addDetail(details, "Rekening cash-out akhir", match.linked_final_bank_account, "mono");
    const matchNote = el("p", "evidence-match", onramp ? "◇ Korelasi escrow dan nominal pada fixture. Profil merchant bukan kesimpulan keterlibatan pidana." : "◇ Reverse lookup menghubungkan deposit kripto, order jual P2P, dan settlement bank akhir.");
    card.append(top, amount, details, matchNote);
    return card;
  }));
}

function dateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("id-ID", { dateStyle: "short", timeStyle: "medium", timeZone: "Asia/Jakarta" }).format(date);
}

function renderActions() {
  const list = $("#action-list");
  const requests = state.graph?.governance_requests || [];
  if (!requests.length) { list.replaceChildren(el("p", "muted small", state.graph?.simulation === false ? "Hasil live tidak menghasilkan tiket freeze otomatis. Identitas, kepemilikan, dan kaitan dengan laporan memerlukan verifikasi institusi." : "Tidak ada tiket rekomendasi: bukti PAKD atau handoff terverifikasi belum tersedia.")); return; }
  const selectedRequest = requests.find((request) => request.request_id === state.selectedRequestId);
  const request = selectedRequest || requests.find((item) => item.target_node_id === state.selected);
  const selectorLabel = el("label", "governance-target-label", "Pilih target permohonan");
  const selector = el("select", "governance-target");
  selector.id = "governance-target";
  if (!request) {
    const option = el("option", "", "Pilih rekening / akun berbukti…"); option.value = ""; selector.append(option);
  }
  for (const item of requests) {
    const option = el("option", "", `${item.institution_name} · ${item.target_node_id} · ${decisionStyle(item).short}`);
    option.value = item.request_id; option.selected = item.request_id === request?.request_id; selector.append(option);
  }
  selector.addEventListener("change", () => {
    const next = requests.find((item) => item.request_id === selector.value);
    state.selectedRequestId = next?.request_id || null;
    if (next) selectNode(next.target_node_id);
  });
  selectorLabel.append(selector);
  list.replaceChildren(selectorLabel);
  if (!request) { list.append(el("p", "muted small", "Simpul terpilih tidak memiliki permohonan. Pilih target lain untuk meninjau alur keputusan institusi.")); return; }
  state.selectedRequestId = request.request_id;
  const current = decisionStyle(request);
  const metadata = request.metadata || {};
  const issuerReview = metadata.request_scope === "ISSUER_REVIEW";
  const available = Number(metadata.available_amount_idr || 0);
  const requested = Number(request.requested_amount_idr || 0);
  const busy = state.busyRequests.has(request.request_id);
  const card = el("article", `governance-card decision-${current.className}`);
  const title = el("div", "governance-title");
  title.append(el("strong", "", request.institution_name), el("span", `status-pill decision-${current.className}`, `${current.icon} ${current.label}`));
  card.append(title);
  const details = el("dl", "detail-list");
  addDetail(details, "ID permohonan", request.request_id, "mono");
  addDetail(details, "Kategori institusi", request.institution_category);
  addDetail(details, "Diminta IASC", idr(request.requested_amount_idr));
  addDetail(details, "Saldo eligible · snapshot demo", idr(available));
  addDetail(details, "Disetujui institusi", idr(request.approved_frozen_amount_idr), "detail-highlight");
  addDetail(details, "Unit pemberi keputusan", request.decided_by_unit);
  addDetail(details, "Catatan keputusan", request.institution_decision_notes);
  addDetail(details, "Waktu keputusan · WIB", request.decision_timestamp ? dateTime(request.decision_timestamp) : null);
  card.append(details);
  if (metadata.request_scope === "TARGETED_ESCROW") card.append(el("p", "scope-note", "Hold hanya untuk sisa escrow/order terkait. Rekening bisnis merchant tetap aktif; dana yang sudah dilepas tidak dihitung kembali."));
  if (metadata.request_scope === "ISSUER_REVIEW") card.append(el("p", "scope-note", "Paket DRAFT untuk review issuer/global exchange. Pengiriman tiket tidak menjalankan smart contract; kewenangan eksekusi berada pada institusi terkait."));
  if (request.request_status === "NOT_SENT") {
    const send = el("button", "button dispatch-button", "📨 Kirim tiket rekomendasi freeze ke institusi");
    send.type = "button"; send.disabled = busy;
    send.addEventListener("click", () => sendFreezeRequest(request));
    card.append(el("p", "governance-stage", "01 · IASC — DISPATCH REKOMENDASI"), send, el("p", "small muted", "Mengirim tiket hanya mengubah status menjadi menunggu keputusan. Nominal disetujui tetap Rp0."));
  }
  if (request.request_status === "SENT_WAITING_DECISION") {
    card.append(el("p", "governance-stage", "02 · KEPUTUSAN MASING-MASING INSTITUSI"));
    const draft = state.decisionDrafts.get(request.request_id) || {};
    const notesLabel = el("label", "", "Catatan peninjauan institusi (simulasi)");
    const notes = el("textarea", "decision-notes"); notes.id = "decision-notes"; notes.rows = 2; notes.maxLength = 500; notes.placeholder = "Opsional · hasil review tim anti-fraud / compliance"; notes.value = draft.notes || ""; notesLabel.append(notes);
    const approve = el("button", "button decision-approve", "✅ Simulasi: institusi setujui freeze");
    approve.type = "button"; approve.disabled = busy || requested <= 0 || available < requested;
    approve.addEventListener("click", () => simulateDecision(request, "APPROVED_FREEZE_BY_INSTITUTION", notes.value));
    const partialLabel = el("label", "", "Nominal freeze sebagian (Rp)");
    const amount = el("input"); amount.type = "number"; amount.id = "partial-amount"; amount.min = "0.01"; amount.step = "0.01";
    amount.max = String(Math.max(0, Math.min(available, requested - .01)));
    amount.value = draft.amount ?? String(Math.floor(Math.min(available, requested) * 50) / 100);
    amount.disabled = busy || issuerReview || requested <= .01 || available <= 0; partialLabel.append(amount);
    const partial = el("button", "button decision-partial", "⚠️ Simulasi: freeze sebagian");
    partial.type = "button"; partial.disabled = amount.disabled;
    partial.addEventListener("click", () => { if (amount.reportValidity()) simulateDecision(request, "PARTIAL_FREEZE_BY_INSTITUTION", notes.value, amount.value); });
    const reject = el("button", "button decision-reject", "❌ Simulasi: ditolak karena saldo kosong");
    reject.type = "button"; reject.disabled = busy;
    reject.addEventListener("click", () => simulateDecision(request, "REJECTED_BY_INSTITUTION", notes.value));
    card.append(notesLabel, approve);
    if (!issuerReview) card.append(partialLabel, partial);
    else card.append(el("p", "scope-note", "Blacklist issuer membatasi token pada seluruh alamat, bukan nominal sebagian. Callback hanya mensimulasikan keputusan institusi; tidak ada transaksi on-chain dikirim."));
    card.append(reject);
    if (available <= 0) card.append(el("p", "scope-note", "Snapshot menunjukkan tidak ada saldo eligible. Permohonan ini hanya dapat didemokan sebagai penolakan."));
  } else if (request.request_status !== "NOT_SENT") card.append(el("p", "scope-note", "Keputusan terminal telah dicatat oleh institusi (simulasi). Muat ulang skenario untuk mendemokan hasil lain."));
  const packet = state.packets.get(request.request_id);
  if (packet) {
    const exportPacket = el("button", "button evidence-download", "↓ Unduh tiket & bukti rantai transaksi");
    exportPacket.type = "button";
    exportPacket.addEventListener("click", () => download(`${state.graph.iasc_ticket_id}-${request.request_id}-DRAFT.json`, packet));
    card.append(exportPacket);
  }
  list.append(card);
}

function applyGovernanceResult(result) {
  const request = result.request;
  if (!request?.request_id) throw new Error("Respons governance tidak memuat ID permohonan.");
  const index = state.graph.governance_requests.findIndex((item) => item.request_id === request.request_id);
  if (index < 0) throw new Error("Permohonan tidak terikat pada trace aktif.");
  state.graph.governance_requests[index] = request;
  if (request.request_status !== "SENT_WAITING_DECISION") state.decisionDrafts.delete(request.request_id);
  const node = state.graph.nodes.find((item) => item.id === request.target_node_id);
  if (node) { node.institution_decision = request; node.status = request.request_status; }
  state.graph.confirmed_frozen_amount_idr = result.confirmed_frozen_amount_idr;
  if (result.evidence_packet) state.packets.set(request.request_id, result.evidence_packet);
  state.selectedRequestId = request.request_id;
  state.currentStep = 4; renderStepper(); renderCaseSummary(); renderMetrics(); renderGraph(); selectNode(request.target_node_id);
}

async function sendFreezeRequest(request) {
  const traceId = state.graph.trace_id;
  if (state.busyRequests.has(request.request_id)) return;
  state.busyRequests.add(request.request_id); renderActions();
  try {
    const result = await api("/api/v1/governance/send-freeze-request", { method: "POST", body: JSON.stringify({ trace_id: traceId, request_id: request.request_id }) });
    if (state.graph?.trace_id !== traceId) return;
    applyGovernanceResult(result);
    log(result.message || `Tiket terkirim ke ${request.institution_name}; menunggu keputusan institusi.`);
    toast(result.message || "Tiket terkirim. Belum ada dana disetujui untuk freeze.");
  } catch (error) { if (state.graph?.trace_id === traceId) toast(error.message, true); }
  finally { if (state.graph?.trace_id === traceId) { state.busyRequests.delete(request.request_id); renderActions(); } }
}

async function simulateDecision(request, decision, notes, partialAmount) {
  const traceId = state.graph.trace_id;
  if (state.busyRequests.has(request.request_id)) return;
  state.decisionDrafts.set(request.request_id, { ...state.decisionDrafts.get(request.request_id), notes, ...(partialAmount !== undefined ? {amount:partialAmount} : {}) });
  const payload = { trace_id: traceId, request_id: request.request_id, decision };
  if (notes.trim()) payload.institution_decision_notes = notes.trim();
  if (partialAmount !== undefined) payload.approved_frozen_amount_idr = partialAmount;
  state.busyRequests.add(request.request_id); renderActions();
  try {
    const result = await api("/api/v1/governance/simulate-institution-decision", { method: "POST", body: JSON.stringify(payload) });
    if (state.graph?.trace_id !== traceId) return;
    applyGovernanceResult(result);
    log(result.message || `${request.institution_name}: ${decisionStyle(result.request).label}.`);
    toast(result.message || "Keputusan institusi tersimpan dalam simulasi.");
  } catch (error) { if (state.graph?.trace_id === traceId) toast(error.message, true); }
  finally { if (state.graph?.trace_id === traceId) { state.busyRequests.delete(request.request_id); renderActions(); } }
}

async function expandNode(node, button) {
  const traceId = state.graph.trace_id;
  button.disabled = true;
  button.textContent = "Mengambil hop berikutnya…";
  try {
    const query = new URLSearchParams({ trace_id: traceId });
    if (node.chain) query.set("chain", node.chain);
    const result = await api(`/api/v1/trail/address/${encodeURIComponent(node.address)}/expand?${query}`);
    if (state.graph?.trace_id !== traceId) return;
    const nodeIds = new Set(state.graph.nodes.map((candidate) => candidate.id));
    const edgeIds = new Set(state.graph.edges.map((edge) => edge.id));
    const addedNodes = (result.nodes || []).filter((candidate) => !nodeIds.has(candidate.id));
    const addedEdges = (result.edges || []).filter((edge) => !edgeIds.has(edge.id));
    state.graph.nodes.push(...addedNodes);
    state.graph.edges.push(...addedEdges);
    stopAnimation();
    renderGraph();
    if (addedNodes.length || addedEdges.length) {
      resetView();
      log(`Expand ${short(node.address, 14)}: ${addedNodes.length} simpul dan ${addedEdges.length} relasi baru.`);
      toast(`${addedNodes.length} simpul baru ditambahkan ke grafik.`);
    } else toast((result.warnings || [])[0] || "Seluruh hop yang tersedia sudah tampil pada grafik ini.");
    if (result.warnings?.length) { $("#warnings").textContent = result.warnings.join(" · "); $("#warnings").hidden = false; }
  } catch (error) { toast(error.message, true); } finally { button.disabled = false; button.textContent = "+ Expand hop on-chain berikutnya"; }
}

function animationPlan() {
  return ForensicGraph.animationSteps(state.graph || {nodes: [], edges: []});
}

function renderFlowStepControls() {
  const steps = animationPlan(), index = state.flowCursor ?? -1;
  $("#flow-prev-step").disabled = index <= 0;
  $("#flow-next-step").disabled = !steps.length || index >= steps.length - 1;
  $("#flow-step-label").textContent = index < 0 ? `${steps.length} tahap` : `${index + 1} / ${steps.length}`;
}

function stopAnimation(reset = true) {
  state.animation++;
  state.cancelFlowParticles?.(); state.cancelFlowParticles = null;
  state.activeAnimationStep = null;
  for (const particle of document.querySelectorAll(".flow-particle")) particle.remove();
  for (const group of document.querySelectorAll(".node,.edge-group")) group.classList.remove("dimmed", "active", "flow-source", "flow-arrived");
  if (reset) state.flowCursor = -1;
  $("#animation-step").hidden = true;
  $("#animate-button").replaceChildren(el("span", "", "▷"), document.createTextNode("Animasi aliran dana"));
  $("#animate-button").dataset.running = "false";
  $("#sidebar-animate-button").textContent = "▶ Putar Animasi Aliran Dana";
  renderFlowStepControls();
}

function prepareFlowAnimation() {
  // Playback shows the whole evidence route so filters cannot hide the victim origin.
  const filtered = state.chainFilter !== "ALL" || state.timeRange !== "ALL" || state.timelineRange || state.flowMode !== "ALL" || state.groupMode !== "CHAIN" || state.focusMainPath;
  if (filtered) {
    state.chainFilter = state.timeRange = state.flowMode = "ALL"; state.timelineRange = null; state.groupMode = "CHAIN"; state.focusMainPath = false;
    $("#focus-main-path").checked = false; $("#owner-chain-toggle").checked = false;
    $("#value-filter-pill").setAttribute("aria-pressed", "false"); $("#flow-filter-pill").textContent = "FLOW ALL"; $("#group-mode-label").textContent = "GROUP BY CHAIN";
    for (const button of document.querySelectorAll("#time-range button")) { const all = button.dataset.days === "ALL"; button.classList.toggle("active", all); button.setAttribute("aria-pressed", String(all)); }
    renderCaseSummary(); renderGraph(); fitGraph();
  }
  if (state.investigationView === "audit") setInvestigationView("flow");
  return animationPlan();
}

function paintAnimationStep(step, arrived) {
  const steps = animationPlan(), visited = new Set(steps.slice(0, state.flowCursor).flatMap(item => [...item.sourceIds, ...item.targetIds]));
  step.sourceIds.forEach(id => visited.add(id));
  if (arrived) step.targetIds.forEach(id => visited.add(id));
  for (const group of document.querySelectorAll(".node")) {
    const id = group.dataset.nodeId;
    if (!arrived) group.classList.remove("selected");
    group.classList.toggle("dimmed", !visited.has(id));
    group.classList.toggle("flow-source", step.sourceIds.includes(id));
    group.classList.toggle("flow-arrived", arrived && step.targetIds.includes(id));
  }
}

function moveFlowParticles(groups, token) {
  const particles = groups.map(group => { const particle = svgEl("circle", {class: "flow-particle", r: 4, "aria-hidden": "true"}); group.append(particle); return {particle, path: group.querySelector(".fan-strand")}; });
  const duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 100 : 1050;
  return new Promise(resolve => {
    let start, frame;
    const finish = success => { cancelAnimationFrame(frame); particles.forEach(({particle}) => particle.remove()); if (state.cancelFlowParticles === cancel) state.cancelFlowParticles = null; resolve(success); };
    const cancel = () => finish(false);
    state.cancelFlowParticles = cancel;
    function tick(time) {
      if (token !== state.animation) { finish(false); return; }
      start ??= time;
      const progress = Math.min(1, (time - start) / duration);
      for (const {particle, path} of particles) { const point = path.getPointAtLength(path.getTotalLength() * progress); particle.setAttribute("transform", `translate(${point.x} ${point.y})`); }
      if (progress >= 1) finish(true); else frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
  });
}

async function playFlowStep(index, token) {
  const steps = animationPlan(), step = steps[index];
  if (!step || token !== state.animation) return false;
  state.flowCursor = index; state.activeAnimationStep = step;
  const ids = new Set(step.edges.map(edge => edge.id)), groups = [];
  for (const group of document.querySelectorAll(".edge-group")) {
    const bundle = state.viewEdges.find(edge => edge.id === group.dataset.edgeId);
    const active = Boolean(bundle && graphMemberEdges(bundle).some(edge => ids.has(edge.id)));
    group.classList.toggle("dimmed", !active); group.classList.remove("active");
    if (active) { group.classList.add("active"); groups.push(group); }
  }
  paintAnimationStep(step, false);
  $("#animation-step").hidden = false;
  $("#animation-step").textContent = `${index + 1} / ${steps.length} · ${step.label} · ${step.edges.length} relasi`;
  if (state.autoFit) fitGraph();
  renderFlowStepControls();
  if (!await moveFlowParticles(groups, token)) return false;
  paintAnimationStep(step, true);
  const target = step.targetIds.find(id => (state.graph.governance_requests || []).some(request => request.target_node_id === id)) || step.targetIds[0];
  if (target) selectNode(target, index === steps.length - 1, true);
  if (index === steps.length - 1) {
    const requests = (state.graph.governance_requests || []).filter(request => step.targetIds.includes(request.target_node_id));
    $("#animation-step").textContent += requests.length ? ` · Rekomendasi freeze: ${requests.map(request => `${idr(request.requested_amount_idr)} · ${friendly(request.request_status)}`).join("; ")}` : " · Tinjau bukti dan rekomendasi yang tersedia";
    if (state.autoFit) fitGraph();
  }
  return true;
}

async function stepFlow(delta = 1) {
  if (!state.graph) return;
  const index = (state.flowCursor ?? -1) + delta;
  stopAnimation(false);
  const steps = prepareFlowAnimation();
  if (index < 0 || index >= steps.length) return;
  await playFlowStep(index, ++state.animation);
}

async function animate() {
  if (!state.graph) return;
  if ($("#animate-button").dataset.running === "true") { stopAnimation(); return; }
  stopAnimation();
  const steps = prepareFlowAnimation();
  if (!steps.length) { toast("Belum ada transfer untuk dianimasikan."); return; }
  const token = ++state.animation;
  $("#animate-button").dataset.running = "true";
  $("#sidebar-animate-button").textContent = "Ⅱ Hentikan Animasi";
  $("#animate-button").replaceChildren(el("span", "", "Ⅱ"), document.createTextNode("Hentikan animasi"));
  log("Animasi kronologis dimulai dari aliran paling hulu.");
  for (let index = 0; index < steps.length; index++) {
    if (!await playFlowStep(index, token)) return;
  }
  if (token === state.animation) {
    $("#animate-button").dataset.running = "false";
    $("#animate-button").replaceChildren(el("span", "", "▷"), document.createTextNode("Ulangi animasi"));
    $("#sidebar-animate-button").textContent = "▶ Ulangi Animasi Aliran Dana";
    log("Animasi selesai. Rekomendasi tetap menunggu keputusan institusi.");
  }
}

function renderTimeline(){
  const edges=transactionRows(state.graph,state.chainFilter,"",state.timeRange),data=ForensicGraph.timeline(edges);state.timelineData=data;
  const svg=$("#activity-timeline"),bars=[],max=Math.max(1,...data.bins.map(bin=>bin.count));
  for(let index=0;index<data.bins.length;index++){const bin=data.bins[index],width=1000/data.bins.length,height=bin.count/max*37;const bar=svgEl("rect",{class:"timeline-bin",x:index*width+1,y:49-height,width:Math.max(1,width-2),height});bar.append(svgEl("title",{},`${bin.count} transaksi · ${dateTime(bin.start)}`));bars.push(bar);}
  if(data.start!=null)for(let index=0;index<7;index++){const time=data.start+(data.end-data.start)*index/6;const format=new Intl.DateTimeFormat("en-US",data.end-data.start>3*86400000?{month:"short",year:"2-digit"}:{month:"short",day:"2-digit",hour:"2-digit",hour12:false});bars.push(svgEl("text",{class:"timeline-label",x:Math.min(955,index*1000/6+3),y:68},format.format(time)));}
  svg.replaceChildren(...bars);$("#timeline-start").disabled=$("#timeline-end").disabled=data.start==null;
  const span=data.end-data.start||1;
  $("#timeline-start").value=state.timelineRange?Math.max(0,(state.timelineRange[0]-data.start)/span*100):0;$("#timeline-end").value=state.timelineRange?Math.min(100,(state.timelineRange[1]-data.start)/span*100):100;
  $("#timeline-range-label").textContent=data.start==null?`No dated transactions · ${data.undatedCount} undated`:state.timelineRange?`${dateTime(state.timelineRange[0])} — ${dateTime(state.timelineRange[1])}`:`${edges.length} records · ${data.undatedCount} undated · all loaded activity`;
}
let timelineFilterTimer;
function filterTimeline(){
  const data=state.timelineData;if(data?.start==null)return;
  let start=Number($("#timeline-start").value),end=Number($("#timeline-end").value);
  if(start>=end){if(document.activeElement===$("#timeline-start"))start=Math.max(0,end-1);else end=Math.min(100,start+1);}
  $("#timeline-start").value=start;$("#timeline-end").value=end;
  state.timelineRange=start===0&&end===100?null:[data.start+(data.end-data.start)*start/100,data.start+(data.end-data.start)*end/100];
  clearTimeout(timelineFilterTimer);timelineFilterTimer=setTimeout(()=>{if(state.graph){stopAnimation();renderGraph();fitGraph();}},80);
}
function renderEntitySearch(){
  const query=$("#entity-search").value.trim().toLowerCase(),results=$("#entity-search-results");
  if(!query||!state.graph){results.hidden=true;return;}
  const matches=state.graph.nodes.filter(node=>[node.label,node.address,node.account,node.asset,node.entity_label,node.metadata?.public_entity?.label].some(value=>String(value||"").toLowerCase().includes(query))||state.graph.edges.some(edge=>[edge.source,edge.target].includes(node.id)&&String(edge.asset||"").toLowerCase()===query)).slice(0,12);
  results.replaceChildren(...matches.map(node=>{const button=el("button","",`${node.metadata?.public_entity?.label||node.label||node.id} · ${short(node.address||node.account||node.chain,20)}`);button.type="button";button.setAttribute("role","option");button.addEventListener("click",()=>{results.hidden=true;setFocalNode(node.id);});return button;}));
  if(!matches.length)results.append(el("p","","Not in this trace. Use Live Scanner to load another address."));results.hidden=false;
}

const dragState = { active: false, moved: false, startX: 0, startY: 0, originX: 0, originY: 0, nodeId: null, edgeId: null };
function svgPoint(event) {
  const svg = $("#graph");
  const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  const matrix = svg.getScreenCTM();
  return matrix ? point.matrixTransform(matrix.inverse()) : point;
}

function bindD3Interactions(){
  state.usingD3=Boolean(window.d3?.drag&&window.d3?.zoom);if(!state.usingD3){state.zoomBehavior=null;return;}
  const d3=window.d3,svg=d3.select($("#graph"));
  state.zoomBehavior=d3.zoom().scaleExtent([.01,4]).filter(event=>event.type==="wheel"||(!event.button&&!event.target.closest(".node,.edge-group"))).on("zoom",event=>{if(event.sourceEvent)state.autoFit=false;state.x=event.transform.x;state.y=event.transform.y;state.scale=event.transform.k;updateTransform(true);});
  svg.call(state.zoomBehavior).on("dblclick.zoom",null);
  d3.select($("#nodes")).selectAll(".node").datum(function(){return state.positions.get(this.dataset.nodeId);}).call(d3.drag().container($("#viewport")).clickDistance(4).on("start",(event,node)=>{event.sourceEvent.stopPropagation();dragState.moved=false;dragState.startX=node.x;dragState.startY=node.y;}).on("drag",(event,node)=>{if(Math.hypot(event.x-dragState.startX,event.y-dragState.startY)>3){dragState.moved=true;movePinnedNode(node.id,event.x,event.y);}}).on("end",(event,node)=>{if(dragState.moved)selectNode(node.id,false);}));
}
$("#graph").addEventListener("pointerdown",event=>{
  if(state.usingD3){dragState.moved=false;return;}if(event.button!==0)return;const point=svgPoint(event);
  Object.assign(dragState,{active:true,moved:false,startX:point.x,startY:point.y,originX:state.x,originY:state.y,nodeId:event.target.closest(".node")?.dataset.nodeId||null,edgeId:event.target.closest(".edge-group")?.dataset.edgeId||null});
  const node=state.positions.get(dragState.nodeId);dragState.nodeStartX=node?.x;dragState.nodeStartY=node?.y;
  $("#graph").setPointerCapture(event.pointerId);
});
$("#graph").addEventListener("pointermove",event=>{
  if(state.usingD3||!dragState.active)return;const point=svgPoint(event),dx=point.x-dragState.startX,dy=point.y-dragState.startY;
  if(Math.abs(dx)+Math.abs(dy)>4)dragState.moved=true;
  if(dragState.moved){if(dragState.nodeId)movePinnedNode(dragState.nodeId,dragState.nodeStartX+dx/state.scale,dragState.nodeStartY+dy/state.scale);else{state.x=dragState.originX+dx;state.y=dragState.originY+dy;updateTransform();}}
});
$("#graph").addEventListener("pointerup",event=>{
  if(state.usingD3)return;dragState.active=false;if($("#graph").hasPointerCapture(event.pointerId))$("#graph").releasePointerCapture(event.pointerId);
  if(!dragState.moved){if(dragState.nodeId)selectNode(dragState.nodeId);else if(dragState.edgeId)selectEdge(dragState.edgeId);}
  else if(dragState.nodeId)selectNode(dragState.nodeId,false);
});
$("#graph").addEventListener("pointercancel",()=>dragState.active=false);
$("#graph").addEventListener("wheel",event=>{if(state.usingD3)return;event.preventDefault();zoom(event.deltaY<0?1.1:1/1.1,svgPoint(event));},{passive:false});
$("#live-form").addEventListener("submit", (event) => { event.preventDefault(); traceLive(); });
for (const mode of ["scenario", "live"]) {
  $(`#${mode}-tab`).addEventListener("click", () => setMode(mode));
  $(`#${mode}-tab`).addEventListener("keydown", (event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? "scenario" : event.key === "End" ? "live" : mode === "scenario" ? "live" : "scenario"; setMode(next); $(`#${next}-tab`).focus(); } });
}
$("#live-chain").addEventListener("change", () => configureLiveChain());
$("#live-preset-button").addEventListener("click", () => { configureLiveChain(); $("#live-wallet").focus(); });
$("#live-wallet").addEventListener("input", () => $("#live-wallet").setCustomValidity(""));
configureLiveChain(false);

function apiLogCard(entry) {
  const card = el("article", "api-log-card");
  const top = el("div", "api-log-head");
  top.append(el("strong", "", `${entry.pakd_code || "PAKD"} · HTTP ${entry.response_status_code ?? "—"}`),el("span", "", `${number(entry.latency_ms, 2)} ms`));
  card.append(top,el("code", "api-endpoint",entry.endpoint_url || "Endpoint tidak tersedia"));
  card.append(el("p", "api-fields", `Scoped fields: ${(entry.scoped_fields_returned || []).join(", ") || "Tidak ada record"}`));
  for (const [label,value] of [["Request JSON",entry.request_payload],["Response JSON · scoped",entry.response_payload]]) {
    const details = el("details", "api-json");
    details.append(el("summary", "",label),el("pre", "",JSON.stringify(value ?? {},null,2).slice(0,12000)));
    card.append(details);
  }
  return card;
}

async function openMirrorDb() {
  const dialog = $("#mirror-dialog");
  if (!dialog.open) dialog.showModal();
  const content = $("#mirror-content");
  content.replaceChildren(el("p", "muted small", "Mengonsumsi API schema PAKD…"));
  try {
    const preview = await api("/api/v1/mirror-db/records");
    const logSection = el("section", "mirror-table-section");
    const logs = state.graph?.pakd_api_exchange_logs || preview.pakd_api_exchange_logs || [];
    logSection.append(el("h3", "", `Log konsumsi REST API PAKD · ${logs.length} panggilan pada trace aktif`),el("p", "mirror-note", "Aplikasi OJK adalah API consumer. Demo menggunakan HTTP ASGI lokal; Scoped Mirror DB hanya dibaca oleh server provider PAKD."));
    if (logs.length) logSection.append(...logs.slice(-80).map(apiLogCard));
    else logSection.append(el("p", "muted small", "Jalankan skenario atau live trace untuk melihat request/response PAKD. Tidak ada log perkara global yang dicampur."));
    const filtering = el("section", "mirror-table-section");
    filtering.append(el("h3", "", "Data filtering · Scoped Mirror DB vs Main DB PAKD"));
    const comparison = el("table", "mirror-table scope-comparison"), head = el("thead"), header = el("tr"), body = el("tbody");
    for (const title of ["Field", "Ruang lingkup", "Status"]) header.append(el("th", "",title));
    head.append(header);
    const included = preview.included_scoped_fields || Object.fromEntries(Object.entries(preview.tables || {}).map(([name,rows])=>[name,[...new Set(rows.flatMap(row=>Object.keys(row)))]]));
    for (const [table,fields] of Object.entries(included)) for (const field of fields) { const row = el("tr"); row.append(el("td", "",field),el("td", "",table),el("td", "scope-included","✅ Disertakan di Scoped Mirror"));body.append(row); }
    for (const field of preview.excluded_internal_pakd_fields || []) { const row = el("tr");row.append(el("td", "",field),el("td", "","Main DB PAKD · nilai tidak diakses"),el("td", "scope-excluded","🔒 Tidak disinkronkan"));body.append(row); }
    comparison.append(head,body);const scroll = el("div", "mirror-table-scroll");scroll.append(comparison);filtering.append(scroll,el("p", "mirror-note", "Minimisasi field adalah rancangan kontrol data. Kebijakan akses, dasar pemrosesan dan kepatuhan operasional tetap memerlukan verifikasi institusi."));
    const sections = Object.entries(preview.tables || {}).map(([name, rows]) => {
      const section = el("section", "mirror-table-section");
      section.append(el("h3", "", `${name} · ${rows.length} rekaman scoped sintetis`));
      if (!rows.length) { section.append(el("p", "muted small", "Tabel kosong.")); return section; }
      const columns = included[name] || [...new Set(rows.flatMap((row) => Object.keys(row)))];
      const scroll = el("div", "mirror-table-scroll"), table = el("table", "mirror-table"), head = el("thead"), heading = el("tr"), body = el("tbody");
      for (const key of columns) { const cell = el("th", "", key); cell.scope = "col"; heading.append(cell); }
      head.append(heading);
      for (const row of rows) { const tr = el("tr"); for (const key of columns) tr.append(el("td", "", row[key] == null ? "—" : typeof row[key] === "object" ? JSON.stringify(row[key]) : String(row[key]))); body.append(tr); }
      table.append(head, body); scroll.append(table); section.append(scroll); return section;
    });
    content.replaceChildren(logSection,filtering,...sections);
  } catch (error) { content.replaceChildren(el("p", "mirror-error", error.message)); }
}
$("#mirror-db-button").addEventListener("click", openMirrorDb);
$("#mirror-close").addEventListener("click", () => $("#mirror-dialog").close());
$("#mirror-dialog").addEventListener("click", (event) => { if (event.target === $("#mirror-dialog")) { const bounds = event.target.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.target.close(); } });

$("#report-form").addEventListener("submit", (event) => { event.preventDefault(); trace(); });
$("#manual-button").addEventListener("click", () => {
  stopAnimation();
  state.scenarioId = null;
  $("#manual-button").textContent = "✓ Handoff manual · aktif";
  $("#manual-button").classList.add("active");
  $(".report-details").open = true;
  $("#form-note").textContent = "Handoff manual belum terverifikasi. Tanpa ledger mitra yang cocok, tidak ada PAKD Trails atau tiket rekomendasi yang direkayasa.";
  $("#iasc_ticket_id").value = `MANUAL-${Date.now()}`;
  drawScenarios();
  trace();
});
$("#d3-library").addEventListener("load",()=>{if(state.graph&&!state.usingD3){renderGraph();fitGraph();}});
$("#zoom-in").addEventListener("click", () => zoom(1.2));
$("#zoom-out").addEventListener("click", () => zoom(1 / 1.2));
$("#fit-button").addEventListener("click",()=>{state.autoFit=true;fitGraph();});
$("#more-info-button").addEventListener("click",()=>{if($("#inspector-drawer").hidden&&!state.selected&&state.focalId)selectNode(state.focalId);else openInspector($("#inspector-drawer").hidden);});
$("#inspector-close").addEventListener("click",()=>{openInspector(false);$("#more-info-button").focus();});
$("#inspector-drawer").addEventListener("keydown",event=>{if(event.key==="Escape"){openInspector(false);$("#more-info-button").focus();}});
$("#value-filter-pill").addEventListener("click",()=>{state.focusMainPath=!state.focusMainPath;$("#focus-main-path").checked=state.focusMainPath;$("#value-filter-pill").setAttribute("aria-pressed",String(state.focusMainPath));if(state.graph){renderGraph();fitGraph();}});
$("#flow-filter-pill").addEventListener("click",()=>{state.flowMode={ALL:"IN",IN:"OUT",OUT:"ALL"}[state.flowMode];$("#flow-filter-pill").textContent={ALL:"FLOW ALL",IN:"INFLOWS ONLY (GREEN)",OUT:"OUTFLOWS ONLY (RED)"}[state.flowMode];if(state.graph){renderGraph();fitGraph();}});
$("#sort-time-pill").addEventListener("click",()=>{state.sortTime=!state.sortTime;$("#sort-time-pill").setAttribute("aria-pressed",String(state.sortTime));$("#sort-time-pill").textContent=state.sortTime?"SORT BY TIME":"DEFAULT ORDER";if(state.graph)renderGraph();});
$("#owner-chain-toggle").addEventListener("change",event=>{state.groupMode=event.target.checked?"OWNER":"CHAIN";$("#group-mode-label").textContent=`GROUP BY ${state.groupMode}`;if(state.graph){renderGraph();fitGraph();}});
$("#clear-focal-button").addEventListener("click",()=>{state.focalId=state.initialFocalId || null;if(state.graph){renderGraph();fitGraph();selectNode(state.focalId,false);}});
$("#freeze-layout-button").addEventListener("click",()=>{state.physicsFrozen=!state.physicsFrozen;state.graphEngine?.setFrozen(state.physicsFrozen);$("#freeze-layout-button").setAttribute("aria-pressed",String(state.physicsFrozen));$("#freeze-layout-button").title=state.physicsFrozen?"Unlock Physics Simulation":"Freeze Layout";$("#visualizer-status").textContent+=state.physicsFrozen?" · layout frozen":" · physics resumed";});
$("#pointer-mode").addEventListener("click",()=>{state.selected=null;state.selectedEdge=null;highlightGraphSelection();$("#pointer-mode").setAttribute("aria-pressed","true");});
$("#entity-search").addEventListener("input",renderEntitySearch);
$("#entity-search").addEventListener("keydown",event=>{if(["Enter","ArrowDown"].includes(event.key)){event.preventDefault();const first=$("#entity-search-results button");if(first){if(event.key==="Enter")first.click();else first.focus();}}else if(event.key==="Escape")$("#entity-search-results").hidden=true;});
for(const id of ["timeline-start","timeline-end"])$("#"+id).addEventListener("input",filterTimeline);
$("#timeline-reset").addEventListener("click",()=>{clearTimeout(timelineFilterTimer);state.timelineRange=null;if(state.graph){renderGraph();fitGraph();}});

$("#flow-view-button").addEventListener("click", () => setInvestigationView("flow"));
$("#audit-view-button").addEventListener("click", () => setInvestigationView("audit"));
$("#focus-main-path").addEventListener("change", (event) => {
  state.focusMainPath = event.target.checked; stopAnimation();
  if (state.graph) { renderGraph(); fitGraph(); renderInspector(); }
});
$("#animate-button").addEventListener("click", animate);
$("#sidebar-animate-button").addEventListener("click", animate);
$("#flow-prev-step").addEventListener("click", () => stepFlow(-1));
$("#flow-next-step").addEventListener("click", () => stepFlow(1));
$("#run-scenario-button").addEventListener("click", trace);
for (const item of document.querySelectorAll(".investigation-stepper li")) {
  item.tabIndex = 0; item.setAttribute("role", "button");
  const focusStep = () => {
    state.currentStep = Number(item.dataset.step); renderStepper();
    if (state.currentStep === 1) { setMode("scenario"); $(".report-details").open = true; $("#report-form").scrollIntoView({ block: "center", behavior: "smooth" }); }
    else if (state.currentStep === 2) openMirrorDb();
    else $(state.currentStep === 3 ? ".graph-panel" : ".right-panel").scrollIntoView({ block: "center", behavior: "smooth" });
  };
  item.addEventListener("click", focusStep);
  item.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); focusStep(); } });
}
$("#export-button").addEventListener("click", () => { if (state.graph) download(`${state.graph.iasc_ticket_id || state.graph.trace_id}-${state.graph.simulation === false ? "live-mainnet" : "bukti-governance-simulasi"}.json`, { ...state.graph, evidence_packets: Object.fromEntries(state.packets) }); });
$("#transaction-search")?.addEventListener("input", renderTransactions);
for (const button of document.querySelectorAll("#time-range button")) {
  button.addEventListener("click", () => {
    state.timeRange = button.dataset.days;
    for (const option of document.querySelectorAll("#time-range button")) { const active = option === button; option.classList.toggle("active", active); option.setAttribute("aria-pressed", String(active)); }
    if (!state.graph) return;
    stopAnimation(); renderGraph(); fitGraph();
    if (state.selected && !state.viewNodes.some((node) => node.id === state.selected)) { state.selected = null; renderInspector(); renderActions(); }
    if (state.selectedEdge && !state.viewEdges.some((edge) => edge.id === state.selectedEdge)) { state.selectedEdge = null; renderInspector(); renderActions(); }
  });
}
for (const [id, target, mode] of [["nav-graph", ".graph-panel"], ["nav-cases", "#case-controls", "scenario"], ["nav-addresses", "#case-controls", "live"], ["nav-evidence", ".right-panel"]]) {
  $(`#${id}`)?.addEventListener("click", () => {
    if (mode) setMode(mode);
    if (id === "nav-evidence") openInspector();
    for (const nav of document.querySelectorAll(".main-navigation button")) { const active = nav.id === id; nav.setAttribute("aria-current", active ? "page" : "false"); nav.classList.toggle("active", active); }
    $(target)?.scrollIntoView({ block: "start", behavior: "smooth" });
    if (id === "nav-addresses") $("#live-wallet").focus({ preventScroll: true });
  });
}

let graphResizeTimer;
new ResizeObserver(() => {
  clearTimeout(graphResizeTimer);
  graphResizeTimer = setTimeout(() => {
    if (state.investigationView === "audit" || !state.graph || Math.abs(Math.max(300, $("#graph-scroll").clientWidth) - state.viewportWidth) < 2 && Math.abs(($("#graph-scroll").clientHeight || 500) - state.viewportHeight) < 2) return;
    stopAnimation();
    renderGraph();
    resetView();
  }, 150);
}).observe($("#graph-board"));

async function init() {
  try {
    const result = await api("/api/v1/trail/scenarios");
    state.scenarios = result.scenarios || [];
    drawScenarios();
    if (!state.scenarios.length) throw new Error("Tidak ada skenario tersedia pada server.");
    if (window.innerWidth < 701) $(".report-details").open = false;
    await chooseScenario(state.scenarios[0]);
  } catch (error) {
    $("#graph-loading").hidden = true;
    $("#scenarios").replaceChildren(el("p", "muted small", "Skenario tidak dapat dimuat. Jalankan server FastAPI lalu muat ulang."));
    toast(error.message, true);
  }
}

init();

function applyTheme(theme) {
  const light = theme === "light";
  document.documentElement.dataset.theme = light ? "light" : "dark";
  $("#theme-toggle").textContent = light ? "☾ Dark" : "☀ Light";
  $("#theme-toggle").setAttribute("aria-label", light ? "Aktifkan Dark mode" : "Aktifkan Light mode");
  $("#theme-toggle").setAttribute("aria-pressed", String(light));
  const graphToggle=$("#graph-theme-toggle");
  if(graphToggle){graphToggle.textContent=light?"☾":"☀";graphToggle.setAttribute("aria-label",light?"Aktifkan Dark mode":"Aktifkan Light mode");graphToggle.setAttribute("aria-pressed",String(light));graphToggle.title=light?"Dark graph":"Light graph";}
}
let savedTheme;
try { savedTheme = localStorage.getItem("crypto-trails-theme"); } catch {}
applyTheme(savedTheme === "light" || savedTheme === "dark" ? savedTheme : window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
function toggleTheme() {
  const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(theme);
  try { localStorage.setItem("crypto-trails-theme", theme); } catch {}
}
$("#theme-toggle").addEventListener("click", toggleTheme);
$("#graph-theme-toggle")?.addEventListener("click", toggleTheme);
