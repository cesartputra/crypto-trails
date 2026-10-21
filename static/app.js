"use strict";

const $ = (selector) => document.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";
const state = { scenarios: [], scenarioId: null, graph: null, selected: null, positions: new Map(), scale: 1, x: 0, y: 0, animation: 0, request: 0, completed: new Map(), logs: [], baseWidth: 1160, baseHeight: 650 };
const idr = (value) => new Intl.NumberFormat("id-ID", { style: "currency", currency: "IDR", maximumFractionDigits: 0 }).format(Number(value || 0));
const number = (value, digits = 2) => new Intl.NumberFormat("id-ID", { maximumFractionDigits: digits }).format(Number(value || 0));
const short = (value, size = 20) => String(value || "").length > size ? `${String(value).slice(0, size - 2)}…` : String(value || "");
const friendly = (value) => String(value || "").replaceAll("_", " ");
const titles = ["P2P full-cycle", "Direct VA & loopback", "DEX & cold wallet", "Golden period"];
const subtitles = ["Deposit → on-chain → cash-out", "Tokocrypto → wallet → Indodax", "P2P → Uniswap → intersepsi", "Intersepsi dalam < 10 menit"];
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
  $("#scenarios").replaceChildren(...state.scenarios.map((scenario, index) => {
    const button = el("button", `scenario-card${state.scenarioId === scenario.id ? " active" : ""}`);
    button.type = "button";
    button.title = scenario.title;
    button.setAttribute("aria-pressed", String(state.scenarioId === scenario.id));
    const copy = el("span", "scenario-copy");
    copy.append(el("strong", "", titles[index] || scenario.title), el("small", "", subtitles[index] || scenario.description));
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
  stopAnimation();
  state.scenarioId = scenario.id;
  $("#manual-button").textContent = "+ Input laporan manual";
  $("#manual-button").classList.remove("active");
  $("#form-note").textContent = "Data sintetis. Pencocokan skenario memerlukan rekening dan nominal yang sesuai.";
  for (const [name, value] of Object.entries(scenario.report)) {
    const input = $("#report-form").elements.namedItem(name);
    if (input) input.value = name === "waktu_transaksi" ? toJakartaInput(value) : value ?? "";
  }
  drawScenarios();
  await trace();
}

async function trace() {
  const form = $("#report-form");
  if (!form.reportValidity()) return;
  const requestId = ++state.request;
  stopAnimation();
  state.graph = null;
  state.selected = null;
  state.completed.clear();
  $("#nodes").replaceChildren();
  $("#edges").replaceChildren();
  $("#route-badges").replaceChildren();
  $("#warnings").hidden = true;
  $("#case-id").textContent = "MENGHUBUNGKAN BUKTI…";
  for (const metric of ["loss", "traced", "hops", "recovery"]) $(`#metric-${metric}`).textContent = "—";
  $("#metric-crypto").textContent = "Menunggu bukti penelusuran";
  $("#metric-recovery-note").textContent = "Estimasi skenario sintetis";
  renderInspector(); renderEvidence(); renderActions();
  $("#graph-loading").hidden = false;
  $("#trace-button").disabled = true;
  const report = Object.fromEntries(new FormData(form));
  report.scenario_id = state.scenarioId;
  report.waktu_transaksi = `${report.waktu_transaksi}+07:00`;
  try {
    const graph = await api("/api/v1/trail/trace-report", { method: "POST", body: JSON.stringify(report) });
    if (requestId !== state.request) return;
    state.graph = graph;
    state.completed.clear();
    state.selected = null;
    state.logs = [];
    renderMetrics();
    renderGraph();
    resetView();
    renderEvidence();
    renderActions();
    const firstAction = (graph.recovery_actions || []).find((action) => action.action_type === "FREEZE_BANK") || (graph.recovery_actions || []).find((action) => action.action_type === "FREEZE_PAKD") || (graph.recovery_actions || [])[0];
    if (firstAction) selectNode(firstAction.node_id);
    else renderInspector();
    log(`${graph.nodes.length} simpul dan ${graph.edges.length} relasi terhubung. Sumber: data simulasi.`);
    if (graph.p2p_detections?.length) log(`${graph.p2p_detections.length} kecocokan escrow P2P ditemukan pada fixture.`);
    const warnings = graph.warnings || [];
    $("#warnings").textContent = warnings.join(" · ");
    $("#warnings").hidden = !warnings.length;
  } catch (error) {
    if (requestId !== state.request) return;
    toast(error.message, true);
    $("#case-id").textContent = "PENELUSURAN BELUM TERSEDIA";
    $("#warnings").textContent = `Penelusuran gagal: ${error.message} Tidak ada bukti atau aksi recovery aktif untuk permintaan ini.`;
    $("#warnings").hidden = false;
    log(`Penelusuran gagal: ${error.message}`);
  } finally {
    if (requestId === state.request) { $("#graph-loading").hidden = true; $("#trace-button").disabled = false; }
  }
}

function renderMetrics() {
  const graph = state.graph;
  $("#case-id").textContent = graph.report_id;
  $("#metric-loss").textContent = idr(graph.total_scam_idr);
  $("#metric-traced").textContent = idr(graph.total_traced_idr);
  $("#metric-crypto").textContent = `${number(graph.total_traced_usdt)} USDT · ekuivalen demo`;
  $("#metric-hops").replaceChildren(document.createTextNode(`${graph.hop_count ?? graph.edges.length} `), el("em", "", "hop"));
  const pct = Number(graph.total_scam_idr) > 0 ? Math.min(100, Number(graph.recoverable_amount_idr) / Number(graph.total_scam_idr) * 100) : 0;
  $("#metric-recovery").textContent = `${number(pct, 1)}%`;
  $("#metric-recovery-note").textContent = `${idr(graph.recoverable_amount_idr)} · estimasi demo`;
  $("#route-badges").replaceChildren(...(graph.route_badges || []).map((badge) => el("span", "route-badge", friendly(badge))));
}

function nodeTheme(node) {
  const type = String(node.node_type || "").toUpperCase();
  if (node.node_zone === "ONCHAIN_WALLET") return { color: "#ad93ff", dark: "#231d3d", icon: type.includes("DEX") ? "⇄" : type.includes("DEPOSIT") ? "▥" : type.includes("BRIDGE") ? "⇆" : "◇", marker: "purple" };
  if (node.node_zone === "CRYPTO_EXCHANGE_BRIDGE" || /MERCHANT|ESCROW|VIRTUAL/.test(type)) return { color: "#efb75f", dark: "#342a1c", icon: /MERCHANT|ESCROW/.test(type) ? "↔" : "▥", marker: "amber" };
  return { color: "#579afa", dark: "#16283f", icon: /VICTIM/.test(type) ? "○" : "▣", marker: "blue" };
}

function layoutNodes() {
  const nodes = state.graph.nodes;
  const edges = state.graph.edges;
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const next = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) if (incoming.has(edge.target) && next.has(edge.source)) { incoming.set(edge.target, incoming.get(edge.target) + 1); next.get(edge.source).push(edge.target); }
  const queue = nodes.filter((node) => incoming.get(node.id) === 0).map((node) => node.id);
  const ranks = new Map(nodes.map((node) => [node.id, 0]));
  for (let index = 0; index < queue.length; index++) for (const target of next.get(queue[index]) || []) {
    ranks.set(target, Math.max(ranks.get(target), ranks.get(queue[index]) + 1));
    incoming.set(target, incoming.get(target) - 1);
    if (incoming.get(target) === 0) queue.push(target);
  }
  const groups = new Map();
  for (const node of nodes) { const rank = ranks.get(node.id); if (!groups.has(rank)) groups.set(rank, []); groups.get(rank).push(node); }
  const maxRank = Math.max(0, ...ranks.values());
  const width = Math.max(300, $("#graph-scroll").clientWidth);
  const columns = Math.min(5, maxRank + 1, Math.max(2, Math.floor((width - 20) / 170)));
  state.baseWidth = width;
  state.baseHeight = Math.max($("#graph-scroll").clientHeight, (Math.floor(maxRank / columns) + 1) * 220 + 10);
  state.labelLength = columns === 1 ? 23 : Math.max(13, Math.min(23, Math.floor((width - 160) / (columns - 1) / 7)));
  state.positions.clear();
  for (const [rank, group] of groups) group.forEach((node, index) => {
    const row = Math.floor(rank / columns);
    const col = row % 2 ? columns - 1 - rank % columns : rank % columns;
    const x = columns === 1 ? width / 2 : 80 + col * (width - 160) / (columns - 1);
    const y = 90 + row * 220 + (index - (group.length - 1) / 2) * 120;
    state.positions.set(node.id, { x, y });
  });
  $("#graph").setAttribute("viewBox", `0 0 ${state.baseWidth} ${state.baseHeight}`);
  $("#graph").style.height = `${state.baseHeight}px`;
}

function renderGraph() {
  if (!state.graph) return;
  layoutNodes();
  const edgeElements = state.graph.edges.map((edge) => {
    const source = state.positions.get(edge.source), target = state.positions.get(edge.target);
    if (!source || !target) return null;
    const fromNode = state.graph.nodes.find((node) => node.id === edge.source);
    const toNode = state.graph.nodes.find((node) => node.id === edge.target);
    const escrow = /ESCROW|SETTLEMENT/.test(edge.edge_type || "");
    const theme = nodeTheme(escrow ? { node_zone: "CRYPTO_EXCHANGE_BRIDGE" } : fromNode?.node_zone === "ONCHAIN_WALLET" ? fromNode : toNode);
    const group = svgEl("g", { class: "edge-group", "data-edge-id": edge.id, "data-source": edge.source, "data-target": edge.target });
    const dx = target.x - source.x, dy = target.y - source.y, length = Math.hypot(dx, dy) || 1;
    const sx = source.x + dx / length * 33, sy = source.y + dy / length * 33;
    const tx = target.x - dx / length * 38, ty = target.y - dy / length * 38;
    const vertical = Math.abs(dx) < 20;
    let path, labelX, labelY;
    if (vertical) {
      path = `M${sx} ${sy} C${sx + 50} ${sy + dy * .3} ${tx + 50} ${ty - dy * .3} ${tx} ${ty}`;
      labelX = sx + 50; labelY = (sy + ty) / 2;
    } else {
      path = `M${sx} ${sy} C${sx + dx * .45} ${sy} ${tx - dx * .45} ${ty} ${tx} ${ty}`;
      labelX = (sx + tx) / 2; labelY = (sy + ty) / 2 - 10;
    }
    const line = svgEl("path", { class: `graph-edge${escrow ? " escrow" : ""}`, d: path, stroke: theme.color, "marker-end": `url(#arrow-${theme.marker})` });
    const label = edge.crypto_amount != null && Number(edge.crypto_amount) > 0 ? `${number(edge.crypto_amount, 0)} ${edge.asset || "USDT"}` : edge.amount_idr != null ? compactIdr(edge.amount_idr) : short(edge.label || friendly(edge.edge_type), 20);
    const text = svgEl("text", { class: `edge-label${escrow ? " escrow" : ""}`, x: labelX, y: labelY }, label);
    group.append(line, text, svgEl("title", {}, `${edge.label || friendly(edge.edge_type)}${edge.tx_hash ? ` · ${edge.tx_hash}` : ""}`));
    return group;
  }).filter(Boolean);
  $("#edges").replaceChildren(...edgeElements);
  $("#nodes").replaceChildren(...state.graph.nodes.map((node) => {
    const { x, y } = state.positions.get(node.id);
    const theme = nodeTheme(node);
    const selected = node.id === state.selected;
    const initial = /INITIAL/i.test(node.node_type || "") || node.metadata?.is_initial_wallet;
    const completed = [...state.completed.values()].find((result) => result.node_id === node.id);
    const completedAction = completed && state.graph.recovery_actions.find((action) => action.id === completed.action_id);
    const preservationOnly = completedAction?.action_type === "TARGETED_ESCROW_HOLD" && Number(completedAction.metadata?.observed_remaining_escrow_idr || 0) === 0;
    const lock = completed && !preservationOnly && !/DRAFT|PACKET|REQUEST/i.test(completed.status || "");
    const group = svgEl("g", { class: `node${selected ? " selected" : ""}`, transform: `translate(${x} ${y})`, "data-node-id": node.id, tabindex: 0, role: "button", "aria-label": `${node.label}. ${friendly(node.node_type)}. Tampilkan detail.`, "aria-pressed": String(selected) });
    group.append(svgEl("circle", { class: "node-halo", r: initial ? 39 : 35, fill: "none", stroke: theme.color, "stroke-width": initial ? 1.5 : .7, opacity: initial ? .6 : .2 }));
    group.append(svgEl("circle", { class: "node-core", r: 26, fill: theme.dark, stroke: lock ? "#54d7a6" : theme.color, "stroke-width": 1.2 }));
    group.append(svgEl("text", { class: "node-icon", y: 1, fill: theme.color }, theme.icon));
    if (initial) group.append(svgEl("text", { class: "initial-label", y: -52 }, "INITIAL WALLET"));
    if (lock) {
      group.append(svgEl("circle", { cx: 23, cy: -21, r: 10, fill: "#163d2d", stroke: "#4caf81", "stroke-width": .8 }));
      group.append(svgEl("text", { class: "node-lock", x: 23, y: -17 }, "✓"));
    }
    group.append(svgEl("text", { class: "node-label", y: 54 }, short(node.label, state.labelLength)));
    const subtitle = node.address ? short(node.address, 18) : node.account ? short(node.account, 18) : short(node.entity_label || friendly(node.node_type), 24);
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

function resetView() { state.scale = 1; state.x = 0; state.y = 0; $("#graph-scroll").scrollTop = 0; updateTransform(); }

function fitGraph() {
  const board = $("#graph-scroll");
  state.scale = Math.max(.2, Math.min(1, (board.clientHeight - 15) / state.baseHeight));
  state.x = state.baseWidth * (1 - state.scale) / 2;
  state.y = 0;
  board.scrollTop = 0;
  updateTransform();
}

function zoom(factor, point = { x: state.baseWidth / 2, y: $("#graph-scroll").scrollTop + $("#graph-scroll").clientHeight / 2 }) {
  const next = Math.max(.2, Math.min(3, state.scale * factor));
  const ratio = next / state.scale;
  state.x = point.x - (point.x - state.x) * ratio;
  state.y = point.y - (point.y - state.y) * ratio;
  state.scale = next;
  updateTransform();
}

function selectNode(id) {
  state.selected = id;
  for (const group of document.querySelectorAll(".node")) {
    const selected = group.dataset.nodeId === id;
    group.classList.toggle("selected", selected);
    group.setAttribute("aria-pressed", String(selected));
  }
  renderInspector();
  renderEvidence();
  renderActions();
}

function addDetail(list, label, value, className = "") {
  if (value === undefined || value === null || value === "") return;
  const row = el("div");
  row.append(el("dt", "", label), el("dd", className, typeof value === "object" ? JSON.stringify(value) : String(value)));
  list.append(row);
}

function renderInspector() {
  const inspector = $("#inspector");
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
  const completed = [...state.completed.values()].find((result) => result.node_id === node.id);
  const completedAction = completed && state.graph.recovery_actions.find((action) => action.id === completed.action_id);
  const preservationOnly = completedAction?.action_type === "TARGETED_ESCROW_HOLD" && Number(completedAction.metadata?.observed_remaining_escrow_idr || 0) === 0;
  const status = el("span", `status-pill${completed ? "" : " pending"}`, completed ? /DRAFT|PACKET|REQUEST/i.test(completed.status || "") ? "◇ Paket permohonan DRAFT" : preservationOnly ? "✓ Bukti order dipreservasi · simulasi" : "✓ Intersepsi tercatat · simulasi" : "● Bukti sintetis · untuk peninjauan");
  const details = el("dl", "detail-list");
  addDetail(details, "Rekening / akun", node.account, "mono");
  addDetail(details, "Alamat blockchain", node.address, "mono");
  addDetail(details, "Jaringan", node.chain);
  addDetail(details, "Nominal terkait", Number(node.amount_idr) > 0 ? idr(node.amount_idr) : null);
  addDetail(details, "Sisa saldo teramati", node.metadata?.balance_status === "NOT_FETCHED" ? "Belum diambil" : node.asset && node.balance_crypto != null ? `${number(node.balance_crypto, 4)} ${node.asset}` : null, "detail-highlight");
  addDetail(details, "Jumlah aset", node.asset && Number(node.crypto_amount) > 0 ? `${number(node.crypto_amount, 4)} ${node.asset}` : null);
  if (node.node_zone === "ONCHAIN_WALLET" && node.taint_score_pct != null) {
    addDetail(details, "Taint score · model demo", `${number(node.taint_score_pct, 1)}%`);
    const meter = el("div", "taint-meter");
    const fill = el("span"); fill.style.width = `${Math.max(0, Math.min(100, Number(node.taint_score_pct)))}%`; meter.append(fill); details.lastElementChild.append(meter);
  }
  else if (node.node_zone === "ONCHAIN_WALLET") addDetail(details, "Taint score", "Belum teratribusi");
  const incoming = state.graph.edges.filter((edge) => edge.target === node.id && edge.tx_hash);
  addDetail(details, "TX hash masuk", incoming.map((edge) => edge.tx_hash).join("\n"), "mono");
  const metadata = node.metadata || {};
  const metadataLabels = { merchant_id: "ID merchant", scammer_uid: "UID akun terkait", uid: "UID akun", kyc_name: "Identitas KYC sintetis", owner_name: "Nama pemilik sintetis", owner: "Nama pemilik sintetis", platform_name: "Platform", platform: "Platform", heuristic_score: "Skor heuristik merchant", p2p_score: "Skor heuristik merchant", confidence: "Keyakinan kecocokan", balance_idr: "Sisa saldo rupiah", available_balance_idr: "Sisa saldo bank teramati", balance_source: "Sumber saldo", order_id: "Order escrow", data_source: "Sumber bukti", evidence_source: "Sumber bukti sintetis", note: "Catatan", notes: "Catatan" };
  for (const [key, value] of Object.entries(metadata)) if (metadataLabels[key]) addDetail(details, metadataLabels[key], /balance_idr/.test(key) ? idr(value) : value, key === "available_balance_idr" ? "detail-highlight" : "");
  if (metadata.kyc) {
    addDetail(details, "Nama KYC sintetis", metadata.kyc.name);
    addDetail(details, "Referensi dokumen KYC", metadata.kyc.document_ref, "mono");
    addDetail(details, "Status KYC fixture", friendly(metadata.kyc.status));
  }
  if (metadata.arkham_entity_profile) {
    const profile = metadata.arkham_entity_profile;
    addDetail(details, "Profil entitas Arkham", typeof profile === "string" ? profile : profile.entity?.name || profile.arkhamEntity?.name || profile.label || profile.name || profile.type || profile.category || JSON.stringify(profile));
  }
  if (metadata.heuristic) {
    addDetail(details, "Skor heuristik merchant", metadata.heuristic.score ?? metadata.heuristic.score_pct);
    for (const [key, value] of Object.entries(metadata.heuristic)) if (!/score|score_pct/.test(key)) addDetail(details, friendly(key), value);
  }
  if (!incoming.length && metadata.tx_hashes) addDetail(details, "TX hashes", Array.isArray(metadata.tx_hashes) ? metadata.tx_hashes.join("\n") : metadata.tx_hashes, "mono");
  if (state.graph.recovery_probability_pct != null) addDetail(details, "Probabilitas recovery · asumsi demo", `${number(state.graph.recovery_probability_pct, 1)}%`);
  inspector.replaceChildren(type, heading, entity, status, details);
  if (node.node_zone === "ONCHAIN_WALLET" && node.address) {
    const expand = el("button", "button expand-button", "+ Expand hop on-chain berikutnya");
    expand.type = "button";
    expand.addEventListener("click", () => expandNode(node, expand));
    inspector.append(expand);
  }
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
  const actions = state.graph?.recovery_actions || [];
  if (!actions.length) { list.replaceChildren(el("p", "muted small", "Belum ada target pemulihan yang didukung bukti.")); return; }
  list.replaceChildren(...actions.map((action) => {
    const issuer = action.action_type === "ISSUER_BLACKLIST_REQUEST";
    const hold = action.action_type === "TARGETED_ESCROW_HOLD";
    const result = state.completed.get(action.id);
    const button = el("button", `action-button${issuer ? " action-issuer" : hold ? " action-hold" : ""}${result ? " done" : ""}`);
    button.type = "button";
    const copy = el("span", "action-copy");
    const title = result ? issuer ? "Unduh ulang paket blacklist DRAFT" : hold ? "Hold / preservasi tercatat · simulasi" : "Intersepsi tercatat · simulasi" : issuer ? `Buat paket ${action.asset || "USDT"} blacklist` : hold ? "Targeted escrow & counterparty hold" : action.action_type === "FREEZE_PAKD" ? "Auto-freeze akun PAKD · simulasi" : "Auto-freeze bank akhir · simulasi";
    const target = state.graph.nodes.find((node) => node.id === action.node_id);
    const amount = action.crypto_amount != null && Number(action.crypto_amount) > 0 ? `${number(action.crypto_amount)} ${action.asset || "USDT"}` : idr(action.amount_idr);
    copy.append(el("strong", "", title), el("small", "", `${target?.label || action.label || "Target berbukti"} · ${amount}`));
    if (hold) {
      const remaining = Number(action.metadata?.observed_remaining_escrow_idr || 0);
      copy.append(el("small", "", remaining > 0 ? `${idr(remaining)} masih di escrow · hold terbatas` : "Escrow telah dilepas · preservasi bukti, tanpa tambahan recovery"));
      copy.append(el("small", "", "Hanya transaksi terkait · lindungi bisnis merchant"));
    }
    button.append(el("span", "action-icon", result ? "✓" : issuer ? "⛓" : hold ? "◈" : "❄"), copy);
    button.title = action.reason || action.label || title;
    button.addEventListener("click", () => executeAction(action, button));
    return button;
  }));
}

async function executeAction(action, button) {
  const traceId = state.graph.trace_id;
  const previous = state.completed.get(action.id);
  if (previous) { if (previous.packet) download(`${state.graph.report_id}-${action.id}-DRAFT.json`, previous.packet); else toast("Aksi simulasi ini sudah tercatat; tidak ada instruksi ganda dikirim."); return; }
  button.disabled = true;
  try {
    const result = await api("/api/v1/recovery/execute-freeze", { method: "POST", body: JSON.stringify({ trace_id: traceId, action_id: action.id }) });
    if (state.graph?.trace_id !== traceId) return;
    state.completed.set(action.id, result);
    const updatedNode = state.graph.nodes.find((node) => node.id === result.node_id);
    if (updatedNode) updatedNode.status = result.status;
    const updatedAction = state.graph.recovery_actions.find((candidate) => candidate.id === result.action_id);
    if (updatedAction) updatedAction.status = result.status;
    log(result.message || `${action.label} · simulasi tercatat.`);
    toast(result.message || "Aksi simulasi berhasil dicatat.");
    if (action.action_type === "ISSUER_BLACKLIST_REQUEST" && result.packet) download(`${state.graph.report_id}-${action.id}-DRAFT.json`, result.packet);
    renderGraph();
    selectNode(action.node_id);
  } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
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
  $("#animate-button").replaceChildren(el("span", "", "▷"), document.createTextNode("Animasi step-by-step"));
  $("#animate-button").dataset.running = "false";
}

async function animate() {
  if (!state.graph) return;
  if ($("#animate-button").dataset.running === "true") { stopAnimation(); return; }
  const token = ++state.animation;
  const hops = new Map(state.graph.nodes.map((node) => [node.id, Number(node.hop || 0)]));
  const edges = [...state.graph.edges].sort((a, b) => (hops.get(a.source) || 0) - (hops.get(b.source) || 0) || (new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime()));
  if (!edges.length) { toast("Belum ada transfer untuk dianimasikan."); return; }
  $("#animate-button").dataset.running = "true";
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

const dragState = { active: false, moved: false, startX: 0, startY: 0, originX: 0, originY: 0, nodeId: null };
function svgPoint(event) {
  const svg = $("#graph");
  const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  const matrix = svg.getScreenCTM();
  return matrix ? point.matrixTransform(matrix.inverse()) : point;
}

$("#graph").addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  const point = svgPoint(event);
  Object.assign(dragState, { active: true, moved: false, startX: point.x, startY: point.y, originX: state.x, originY: state.y, nodeId: event.target.closest(".node")?.dataset.nodeId || null });
  $("#graph").setPointerCapture(event.pointerId);
});
$("#graph").addEventListener("pointermove", (event) => {
  if (!dragState.active) return;
  const point = svgPoint(event), dx = point.x - dragState.startX, dy = point.y - dragState.startY;
  if (Math.abs(dx) + Math.abs(dy) > 5) dragState.moved = true;
  if (dragState.moved) { state.x = dragState.originX + dx; state.y = dragState.originY + dy; updateTransform(); }
});
$("#graph").addEventListener("pointerup", (event) => { dragState.active = false; if ($("#graph").hasPointerCapture(event.pointerId)) $("#graph").releasePointerCapture(event.pointerId); if (!dragState.moved && dragState.nodeId) selectNode(dragState.nodeId); });
$("#graph").addEventListener("pointercancel", () => { dragState.active = false; });
$("#graph").addEventListener("wheel", (event) => { if (!event.ctrlKey && !event.metaKey) return; event.preventDefault(); zoom(event.deltaY < 0 ? 1.1 : 1 / 1.1, svgPoint(event)); }, { passive: false });
$("#report-form").addEventListener("submit", (event) => { event.preventDefault(); trace(); });
$("#manual-button").addEventListener("click", () => {
  stopAnimation();
  state.scenarioId = null;
  $("#manual-button").textContent = "✓ Laporan manual · aktif";
  $("#manual-button").classList.add("active");
  $(".report-details").open = true;
  $("#form-note").textContent = "Laporan manual hanya menghasilkan simpul awal. Mutasi bank dan data exchange eksternal belum terhubung.";
  $("#report_id").value = `MANUAL-${Date.now()}`;
  drawScenarios();
  trace();
});
$("#zoom-in").addEventListener("click", () => zoom(1.2));
$("#zoom-out").addEventListener("click", () => zoom(1 / 1.2));
$("#fit-button").addEventListener("click", fitGraph);
$("#animate-button").addEventListener("click", animate);
$("#export-button").addEventListener("click", () => { if (state.graph) download(`${state.graph.report_id}-bukti-simulasi.json`, { ...state.graph, executed_simulations: [...state.completed.values()] }); });

let graphResizeTimer;
new ResizeObserver(() => {
  clearTimeout(graphResizeTimer);
  graphResizeTimer = setTimeout(() => {
    if (!state.graph || Math.abs($("#graph-scroll").clientWidth - state.baseWidth) < 2) return;
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
