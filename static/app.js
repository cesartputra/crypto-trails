"use strict";

const $ = (selector) => document.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";
const state = { scenarios: [], scenarioId: null, graph: null, selected: null, selectedEdge: null, mode: "scenario", positions: new Map(), scale: 1, x: 0, y: 0, animation: 0, request: 0, packets: new Map(), busyRequests: new Set(), selectedRequestId: null, decisionDrafts: new Map(), logs: [], baseWidth: 1160, baseHeight: 650, viewportWidth: 0, chainFilter: "ALL", viewNodes: [], viewEdges: [], branchByNode: new Map(), lanes: [], currentStep: 3 };
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
  state.graph = null;
  state.selected = null;
  state.selectedEdge = null;
  state.packets.clear();
  state.decisionDrafts.clear();
  state.busyRequests.clear();
  state.selectedRequestId = null;
  $("#nodes").replaceChildren();
  $("#edges").replaceChildren();
  $("#route-badges").replaceChildren();
  $("#swimlanes").replaceChildren();
  $("#chain-dispersal-cards").replaceChildren();
  $("#chain-filters").replaceChildren();
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
  state.currentStep = 3;
  renderCaseSummary(); renderStepper();
  renderMetrics(); renderGraph(); resetView(); renderEvidence(); renderPakd(); renderActions();
  const firstRequest = (graph.governance_requests || []).find((request) => request.institution_category === "PUJK" && Number(request.requested_amount_idr) > 0) || (graph.governance_requests || []).find((request) => Number(request.requested_amount_idr) > 0) || (graph.governance_requests || [])[0];
  if (firstRequest) selectNode(firstRequest.target_node_id);
  else if (graph.nodes.length) selectNode(graph.nodes.find((node) => node.metadata?.is_seed_wallet || node.metadata?.is_initial_wallet || /SEED|INITIAL/.test(node.node_type))?.id || graph.nodes[0].id);
  else renderInspector();
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
  const budget = metadata.requests_made === undefined ? "dibatasi anggaran API" : `${metadata.requests_made} permintaan API${maxHops > 3 ? `, ${metadata.cryptoapis_requests_made ?? 0} ke Crypto APIs` : ""}`;
  return `${maxHops} hop · hop 1–${Math.min(maxHops, 3)}: ${primary}${continuation} · ${budget}`;
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

function nodeTheme(node) {
  const type = String(node.node_type || "").toUpperCase();
  if (node.is_bridge_node || /BRIDGE_PROTOCOL|BRIDGE_CONTRACT/.test(type)) return { color: "#57d8ef", dark: "#12323d", icon: "🌉", marker: "cyan" };
  if (node.node_zone === "ONCHAIN_WALLET") return { color: chainInfo(node)?.color || "#ad93ff", dark: "#231d3d", icon: type.includes("TRANSACTION") ? "⇢" : type.includes("DEX") ? "⇄" : type.includes("DEPOSIT") ? "▥" : type.includes("BRIDGE") ? "⇆" : "◇", marker: "purple" };
  if (node.node_zone === "CRYPTO_EXCHANGE_BRIDGE") return { color: "#efb75f", dark: "#342a1c", icon: /MERCHANT|ESCROW/.test(type) ? "↔" : "▥", marker: "amber" };
  return { color: "#579afa", dark: "#16283f", icon: /VICTIM/.test(type) ? "○" : /MERCHANT/.test(type) ? "↔" : "▣", marker: "blue" };
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
  const graph = state.graph, byId = new Map(graph.nodes.map((node) => [node.id, node]));
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
  const requestTargets = new Set((graph.governance_requests || []).map((request) => request.target_node_id));
  const hidden = new Set(graph.nodes.filter((node) => /^(PAKD_FIAT_DEPOSIT|PAKD_CRYPTO_CONVERSION)$/.test(node.node_type) && !requestTargets.has(node.id)).map((node) => node.id));
  const visible = graph.nodes.filter((node) => !hidden.has(node.id) && (state.chainFilter === "ALL" || !branches.get(node.id) || branches.get(node.id) === state.chainFilter));
  const visibleIds = new Set(visible.map((node) => node.id)), edges = [];
  const outgoing = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) if (outgoing.has(edge.source)) outgoing.get(edge.source).push(edge);
  for (const node of visible) {
    const walk = (edge, path, visited) => {
      if (visibleIds.has(edge.target)) {
        edges.push(path.length ? { ...edge, id: `summary:${[...path, edge.id].join(":")}`, source: node.id, label: "Pencocokan & konversi PAKD", metadata: { ...edge.metadata, collapsed_api_steps: [...path, edge.id] } } : edge);
      } else if (hidden.has(edge.target) && !visited.has(edge.target)) {
        const nextVisited = new Set(visited); nextVisited.add(edge.target);
        for (const next of outgoing.get(edge.target) || []) walk(next, [...path, edge.id], nextVisited);
      }
    };
    for (const edge of outgoing.get(node.id) || []) walk(edge, [], new Set([node.id]));
  }
  state.branchByNode = branches;
  return { nodes: visible, edges };
}
function layoutNodes() {
  const view = visibleGraph(); state.viewNodes = view.nodes; state.viewEdges = view.edges;
  state.viewportWidth = Math.max(300, $("#graph-scroll").clientWidth);
  const shared = view.nodes.filter((node) => !state.branchByNode.get(node.id));
  const sharedRanks = [...new Set(shared.map((node) => Number(node.hop) || 0))].sort((a, b) => a - b);
  const sharedWidth = shared.length ? Math.max(325, 90 + sharedRanks.length * 145) : 0;
  const branchNames = caseChains().filter((chain) => state.chainFilter === "ALL" || state.chainFilter === chain);
  state.positions.clear(); state.lanes = [];
  let top = 58, furthest = sharedWidth + 180;
  for (const chain of branchNames) {
    const nodes = view.nodes.filter((node) => state.branchByNode.get(node.id) === chain), groups = new Map();
    for (const node of nodes) { const hop = Number(node.hop) || 0; if (!groups.has(hop)) groups.set(hop, []); groups.get(hop).push(node); }
    const ranks = [...groups.keys()].sort((a, b) => a - b);
    const height = Math.max(225, ...[...groups.values()].map((group) => group.length * 155 + 70));
    state.lanes.push({ chain, top, height });
    ranks.forEach((rank, column) => groups.get(rank).forEach((node, index, group) => {
      state.positions.set(node.id, { x: sharedWidth + 100 + column * 185, y: top + height / 2 - 20 + (index - (group.length - 1) / 2) * 155 });
    }));
    furthest = Math.max(furthest, sharedWidth + 100 + Math.max(0, ranks.length - 1) * 185 + 110);
    top += height + 14;
  }
  state.baseHeight = Math.max(300, top + 10, ...sharedRanks.map((rank) => shared.filter((node) => (Number(node.hop) || 0) === rank).length * 160 + 70));
  sharedRanks.forEach((rank, column) => {
    const group = shared.filter((node) => (Number(node.hop) || 0) === rank);
    group.forEach((node, index) => state.positions.set(node.id, { x: 80 + column * 145, y: state.baseHeight / 2 - 20 + (index - (group.length - 1) / 2) * 160 }));
  });
  state.baseWidth = Math.max(400, furthest); state.labelLength = 21;
  const svg = $("#graph"); svg.setAttribute("viewBox", `0 0 ${state.viewportWidth} ${$("#graph-scroll").clientHeight || 500}`); svg.style.height = "100%"; svg.style.width = "100%";
  const labels = [];
  if (shared.length) labels.push(svgEl("text", { x: 25, y: 25, class: "swimlane-heading" }, "HANDOFF FNA → PAKD MIRROR API"));
  for (const lane of state.lanes) {
    const info = chainInfo(lane.chain);
    labels.push(svgEl("rect", { x: sharedWidth + 8, y: lane.top, width: state.baseWidth - sharedWidth - 20, height: lane.height, rx: 14, class: "swimlane-bg", fill: info?.color, "fill-opacity": .035, stroke: info?.color, "stroke-opacity": .18 }));
    if (info?.icon) labels.push(svgEl("image", { href: info.icon, x: sharedWidth + 23, y: lane.top + 9, width: 24, height: 24, class: "chain-lane-icon", "clip-path": "url(#web3icon-round)", "aria-hidden": "true" }));
    labels.push(svgEl("text", { x: sharedWidth + (info?.icon ? 57 : 25), y: lane.top + 25, class: "swimlane-heading", fill: info?.color }, `${lane.chain} · ${lane.chain === "BTC" ? "Bitcoin Scanner" : lane.chain === "SOLANA" ? "Solana Scanner" : "EVM Scanner"}`));
  }
  $("#swimlanes").replaceChildren(...labels);
}

function edgeGeometry(source, target, index = 0, count = 1) {
  const dx = target.x - source.x, dy = target.y - source.y, length = Math.hypot(dx, dy);
  if (length < 1) {
    const radius = 75 + index * 30;
    return { path: `M${source.x + 22} ${source.y - 20} C${source.x + radius} ${source.y - radius} ${source.x + radius} ${source.y + radius} ${source.x + 25} ${source.y + 15}`, labelX: source.x + radius * .8, labelY: source.y - 24 - index * 20 };
  }
  const sx = source.x + dx / length * 33, sy = source.y + dy / length * 33;
  const tx = target.x - dx / length * 38, ty = target.y - dy / length * 38;
  const offset = count > 1 ? (index - (count - 1) / 2) * 56 : 0;
  const bendX = -dy / length * offset, bendY = dx / length * offset;
  if (Math.abs(dx) < 20) return {
    path: `M${sx} ${sy} C${sx + 50 + bendX} ${sy + dy * .3 + bendY} ${tx + 50 + bendX} ${ty - dy * .3 + bendY} ${tx} ${ty}`,
    labelX: sx + 50 + bendX * .75, labelY: (sy + ty) / 2 + bendY * .75 + offset * .5,
  };
  return {
    path: `M${sx} ${sy} C${sx + dx * .45 + bendX} ${sy + bendY} ${tx - dx * .45 + bendX} ${ty + bendY} ${tx} ${ty}`,
    labelX: (sx + tx) / 2 + bendX * .75, labelY: (sy + ty) / 2 - 10 + bendY * .75,
  };
}

function renderGraph() {
  if (!state.graph) return;
  layoutNodes();
  const parallelEdges = new Map();
  for (const edge of state.viewEdges) {
    const key = JSON.stringify([edge.source, edge.target]);
    if (!parallelEdges.has(key)) parallelEdges.set(key, []);
    parallelEdges.get(key).push(edge.id);
  }
  const edgeElements = state.viewEdges.map((edge) => {
    const source = state.positions.get(edge.source), target = state.positions.get(edge.target);
    if (!source || !target) return null;
    const fromNode = state.graph.nodes.find((node) => node.id === edge.source);
    const toNode = state.graph.nodes.find((node) => node.id === edge.target);
    const escrow = /ESCROW|SETTLEMENT/.test(edge.edge_type || "");
    const crossChain = isBridgeEdge(edge);
    const candidate = isCandidateEdge(edge);
    const theme = crossChain ? {color:"#57d8ef",marker:"cyan"} : nodeTheme(escrow ? { node_zone: "CRYPTO_EXCHANGE_BRIDGE" } : fromNode?.node_zone === "ONCHAIN_WALLET" ? fromNode : toNode);
    const group = svgEl("g", { class: `edge-group${crossChain ? " cross-chain-edge" : ""}${candidate ? " bridge-candidate" : ""}${state.selectedEdge === edge.id ? " selected" : ""}`, "data-edge-id": edge.id, "data-source": edge.source, "data-target": edge.target, tabindex: 0, role: "button", "aria-label": `Inspeksi transaksi ${edge.label || edge.asset || edge.id}`, "aria-pressed": String(state.selectedEdge === edge.id) });
    const siblings = parallelEdges.get(JSON.stringify([edge.source, edge.target]));
    const { path, labelX, labelY } = edgeGeometry(source, target, siblings.indexOf(edge.id), siblings.length);
    const line = svgEl("path", { class: `graph-edge${crossChain ? " bridge-path" : ""}${escrow ? " escrow" : ""}`, d: path, stroke: theme.color, "marker-end": `url(#arrow-${theme.marker})` });
    const label = crossChain ? `${candidate ? "KANDIDAT" : "BRIDGE"}: ${chainInfo(edge.bridged_from_chain)?.badge || chainInfo(fromNode)?.badge || "?"} → ${chainInfo(edge.bridged_to_chain)?.badge || chainInfo(toNode)?.badge || "?"}` : edge.crypto_amount != null && Number(edge.crypto_amount) > 0 ? `${number(edge.crypto_amount, 4)} ${edge.asset || "USDT"}` : Number(edge.amount_idr) > 0 ? compactIdr(edge.amount_idr) : short(edge.label || friendly(edge.edge_type), 20);
    const text = svgEl("text", { class: `edge-label${escrow ? " escrow" : ""}`, x: labelX, y: labelY }, label);
    const hit = svgEl("path", { class: "edge-hit", d: path });
    group.append(hit, line, text, svgEl("title", {}, `${edge.label || friendly(edge.edge_type)}${edge.tx_hash ? ` · ${edge.tx_hash}` : ""}`));
    group.addEventListener("click", (event) => { if (!dragState.moved) { event.stopPropagation(); selectEdge(edge.id); } });
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectEdge(edge.id); } });
    return group;
  }).filter(Boolean);
  $("#edges").replaceChildren(...edgeElements);
  $("#nodes").replaceChildren(...state.viewNodes.map((node) => {
    const { x, y } = state.positions.get(node.id);
    const theme = nodeTheme(node);
    const selected = node.id === state.selected;
    const chain = chainInfo(node);
    const initial = /INITIAL/i.test(node.node_type || "") || node.metadata?.is_initial_wallet;
    const decision = decisionForNode(node);
    const decisionTheme = decisionStyle(decision);
    const lock = decision && Number(decision.approved_frozen_amount_idr) > 0;
    const group = svgEl("g", { class: `node${selected ? " selected" : ""}`, transform: `translate(${x} ${y})`, "data-node-id": node.id, tabindex: 0, role: "button", "aria-label": `${node.label}. ${friendly(node.node_type)}. Tampilkan detail.`, "aria-pressed": String(selected) });
    group.append(svgEl("circle", { class: "node-halo", r: initial ? 39 : 35, fill: "none", stroke: theme.color, "stroke-width": initial ? 1.5 : .7, opacity: initial ? .6 : .2 }));
    group.append(svgEl("circle", { class: "node-core", r: 26, fill: theme.dark, stroke: lock ? decisionTheme.color : theme.color, "stroke-width": 1.2 }));
    if (node.node_zone === "ONCHAIN_WALLET" && chain?.icon) {
      group.append(svgEl("image", { href: chain.icon, x: -20, y: -20, width: 40, height: 40, class: "node-chain-icon", "clip-path": "url(#web3icon-round)", "aria-hidden": "true" }));
    } else {
      group.append(svgEl("text", { class: "node-icon", y: 1, fill: theme.color }, theme.icon));
    }
    if (initial) group.append(svgEl("text", { class: "initial-label", y: -52 }, "WALLET AWAL"));
    if (lock) {
      group.append(svgEl("circle", { cx: 23, cy: -21, r: 10, fill: "#163d2d", stroke: "#4caf81", "stroke-width": .8 }));
      group.append(svgEl("text", { class: "node-lock", x: 23, y: -17, fill: decisionTheme.color }, decisionTheme.icon));
    }
    if (decision) {
      const badgeY = initial ? -79 : -55;
      group.append(svgEl("rect", { x: -49, y: badgeY, width: 98, height: 17, rx: 4, fill: "#101b2a", stroke: decisionTheme.color, "stroke-width": .6 }));
      group.append(svgEl("text", { class: "decision-node-badge", x: 0, y: badgeY + 11.5, fill: decisionTheme.color }, decisionTheme.short));
    }
    if (node.node_zone === "ONCHAIN_WALLET" && chain) {
      group.append(svgEl("rect", {class:"chain-badge-bg",x:17,y:-42,width:44,height:16,rx:4,fill:"#0b1827",stroke:chain.color,"stroke-width":.7}));
      group.append(svgEl("text", {class:"chain-badge",x:39,y:-31,fill:chain.color},chain.badge));
    }
    if (node.is_bridge_node && node.bridged_to_chain) group.append(svgEl("text", {class:"bridge-route-label",y:99,fill:"#81e1ed"}, `${chainInfo(node.bridged_from_chain || node.chain)?.badge || "?"} → ${chainInfo(node.bridged_to_chain)?.badge || "?"}`));
    group.append(svgEl("text", { class: "node-label", y: 54 }, short(node.label, state.labelLength)));
    const subtitle = node.asset && Number(node.crypto_amount) > 0 ? `${number(node.crypto_amount, 5)} ${node.asset}` : Number(node.amount_idr) > 0 ? compactIdr(node.amount_idr) : node.account ? short(node.account, 18) : node.address ? short(node.address, 18) : short(node.entity_label || friendly(node.node_type), 22);
    group.append(svgEl("text", { class: "node-subtitle", y: 70 }, subtitle));
    if (node.node_zone === "ONCHAIN_WALLET" && node.taint_score_pct != null) group.append(svgEl("text", { class: "taint-label", y: 84 }, `TAINT ${number(node.taint_score_pct, 1)}%`));
    else if (node.node_zone === "ONCHAIN_WALLET") group.append(svgEl("text", { class: "taint-label", y: 84 }, "BELUM TERATRIBUSI"));
    group.append(svgEl("title", {}, `${node.label}\n${node.address || node.account || node.entity_label || ""}`));
    group.addEventListener("click", (event) => { if (!dragState.moved) { event.stopPropagation(); selectNode(node.id); } });
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(node.id); } });
    return group;
  }));
  updateTransform();
}

function compactIdr(value) {
  const amount = Number(value || 0);
  return amount >= 1e9 ? `Rp ${number(amount / 1e9, 1)} M` : amount >= 1e6 ? `Rp ${number(amount / 1e6, 1)} jt` : idr(amount);
}

function updateTransform() {
  $("#viewport").setAttribute("transform", `translate(${state.x} ${state.y}) scale(${state.scale})`);
  $("#zoom-level").textContent = `${Math.round(state.scale * 100)}%`;
}

function resetView() {
  fitGraph();
}

function fitGraph() {
  const board = $("#graph-scroll");
  state.scale = Math.max(.01, Math.min(1, (board.clientWidth - 20) / state.baseWidth, (board.clientHeight - 15) / state.baseHeight));
  state.x = (board.clientWidth - state.baseWidth * state.scale) / 2;
  state.y = Math.max(0, (board.clientHeight - state.baseHeight * state.scale) / 2);
  board.scrollTop = 0;
  board.scrollLeft = 0;
  updateTransform();
}

function zoom(factor, point = { x: $("#graph-scroll").clientWidth / 2, y: $("#graph-scroll").scrollTop + $("#graph-scroll").clientHeight / 2 }) {
  const next = Math.max(.01, Math.min(3, state.scale * factor));
  const ratio = next / state.scale;
  state.x = point.x - (point.x - state.x) * ratio;
  state.y = point.y - (point.y - state.y) * ratio;
  state.scale = next;
  updateTransform();
}

function selectNode(id) {
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
  renderInspector();
  renderEvidence();
  renderPakd();
  renderActions();
}

function selectEdge(id) {
  state.selectedEdge = id;
  state.selected = null;
  state.selectedRequestId = null;
  for (const group of document.querySelectorAll(".node")) { group.classList.remove("selected"); group.setAttribute("aria-pressed", "false"); }
  for (const group of document.querySelectorAll(".edge-group")) { const selected = group.dataset.edgeId === id; group.classList.toggle("selected", selected); group.setAttribute("aria-pressed", String(selected)); }
  renderInspector(); renderActions();
}

function addDetail(list, label, value, className = "") {
  if (value === undefined || value === null || value === "") return;
  const row = el("div");
  row.append(el("dt", "", label), el("dd", className, typeof value === "object" ? JSON.stringify(value) : String(value)));
  list.append(row);
}

function renderInspector() {
  const inspector = $("#inspector");
  const edge = state.viewEdges.find((candidate) => candidate.id === state.selectedEdge) || state.graph?.edges.find((candidate) => candidate.id === state.selectedEdge);
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
  const node = state.graph?.nodes.find((candidate) => candidate.id === state.selected);
  if (!node) {
    const empty = el("div", "inspector-empty");
    empty.append(el("span", "", "⌖"), el("h3", "", "Inspeksi simpul"), el("p", "", "Pilih rekening, escrow, atau wallet pada grafik untuk melihat bukti."));
    inspector.replaceChildren(empty);
    return;
  }
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
    addDetail(details, "Institusi pemegang keputusan", `${decision.institution_category} · ${decision.institution_name}`);
    addDetail(details, "Konteks keputusan", decision.metadata?.historical ? "Riwayat pada snapshot FNA; terpisah dari tiket rekomendasi baru" : "Tiket governance pada trace aktif · simulasi");
    addDetail(details, "Catatan institusi", decision.institution_decision_notes);
    addDetail(details, "Unit pemberi keputusan", decision.decided_by_unit);
    addDetail(details, "Nominal disetujui institusi", idr(decision.approved_frozen_amount_idr));
  }
  inspector.replaceChildren(type, heading, entity, status, details);
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

function stopAnimation() {
  state.animation++;
  for (const group of document.querySelectorAll(".node,.edge-group")) group.classList.remove("dimmed", "active");
  $("#animation-step").hidden = true;
  $("#animate-button").replaceChildren(el("span", "", "▷"), document.createTextNode("Animasi aliran dana"));
  $("#animate-button").dataset.running = "false";
  $("#sidebar-animate-button").textContent = "▶ Putar Animasi Aliran Dana";
}

async function animate() {
  if (!state.graph) return;
  if ($("#animate-button").dataset.running === "true") { stopAnimation(); return; }
  const token = ++state.animation;
  const hops = new Map(state.graph.nodes.map((node) => [node.id, Number(node.hop || 0)]));
  const edges = [...state.viewEdges].sort((a, b) => (hops.get(a.source) || 0) - (hops.get(b.source) || 0) || (new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime()));
  if (!edges.length) { toast("Belum ada transfer untuk dianimasikan."); return; }
  $("#animate-button").dataset.running = "true";
  $("#sidebar-animate-button").textContent = "Ⅱ Hentikan Animasi";
  $("#animate-button").replaceChildren(el("span", "", "Ⅱ"), document.createTextNode("Hentikan animasi"));
  for (const group of document.querySelectorAll(".node,.edge-group")) group.classList.add("dimmed");
  $("#animation-step").hidden = false;
  log("Animasi penelusuran dimulai.");
  for (let index = 0; index < edges.length; index++) {
    if (token !== state.animation) return;
    const edge = edges[index];
    for (const group of document.querySelectorAll(".edge-group")) if (group.dataset.edgeId === edge.id) { group.classList.remove("dimmed"); group.classList.add("active"); }
    for (const group of document.querySelectorAll(".node")) if ([edge.source, edge.target].includes(group.dataset.nodeId)) group.classList.remove("dimmed");
    $("#animation-step").textContent = `${String(index + 1).padStart(2, "0")} / ${edges.length} · ${friendly(edge.edge_type)}`;
    selectNode(edge.target);
    const position = state.positions.get(edge.target);
    if (position) $("#graph-scroll").scrollTo({ top: Math.max(0, position.y * state.scale + state.y - $("#graph-scroll").clientHeight / 2), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    await new Promise((resolve) => setTimeout(resolve, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 100 : 1050));
  }
  if (token === state.animation) { stopAnimation(); log("Animasi selesai. Semua relasi tersedia untuk diinspeksi."); }
}

const dragState = { active: false, moved: false, startX: 0, startY: 0, originX: 0, originY: 0, nodeId: null, edgeId: null };
function svgPoint(event) {
  const svg = $("#graph");
  const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  const matrix = svg.getScreenCTM();
  return matrix ? point.matrixTransform(matrix.inverse()) : point;
}

$("#graph").addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  const point = svgPoint(event);
  Object.assign(dragState, { active: true, moved: false, startX: point.x, startY: point.y, originX: state.x, originY: state.y, nodeId: event.target.closest(".node")?.dataset.nodeId || null, edgeId: event.target.closest(".edge-group")?.dataset.edgeId || null });
  $("#graph").setPointerCapture(event.pointerId);
});
$("#graph").addEventListener("pointermove", (event) => {
  if (!dragState.active) return;
  const point = svgPoint(event), dx = point.x - dragState.startX, dy = point.y - dragState.startY;
  if (Math.abs(dx) + Math.abs(dy) > 5) dragState.moved = true;
  if (dragState.moved) { state.x = dragState.originX + dx; state.y = dragState.originY + dy; updateTransform(); }
});
$("#graph").addEventListener("pointerup", (event) => {
  dragState.active = false;
  if ($("#graph").hasPointerCapture(event.pointerId)) $("#graph").releasePointerCapture(event.pointerId);
  if (!dragState.moved) {
    if (dragState.nodeId) selectNode(dragState.nodeId);
    else if (dragState.edgeId) selectEdge(dragState.edgeId);
  }
});
$("#graph").addEventListener("pointercancel", () => { dragState.active = false; });
$("#graph").addEventListener("wheel", (event) => { if (!event.ctrlKey && !event.metaKey) return; event.preventDefault(); zoom(event.deltaY < 0 ? 1.1 : 1 / 1.1, svgPoint(event)); }, { passive: false });
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
$("#zoom-in").addEventListener("click", () => zoom(1.2));
$("#zoom-out").addEventListener("click", () => zoom(1 / 1.2));
$("#fit-button").addEventListener("click", fitGraph);
$("#animate-button").addEventListener("click", animate);
$("#sidebar-animate-button").addEventListener("click", animate);
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

let graphResizeTimer;
new ResizeObserver(() => {
  clearTimeout(graphResizeTimer);
  graphResizeTimer = setTimeout(() => {
    if (!state.graph || Math.abs($("#graph-scroll").clientWidth - state.viewportWidth) < 2) return;
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
}
let savedTheme;
try { savedTheme = localStorage.getItem("crypto-trails-theme"); } catch {}
applyTheme(savedTheme === "light" || savedTheme === "dark" ? savedTheme : window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
$("#theme-toggle").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(theme);
  try { localStorage.setItem("crypto-trails-theme", theme); } catch {}
});
