// Browser-free projection/security regression: node tests/frontend_guided_selfcheck.cjs
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../static/app.js'), 'utf8');
const html = readFileSync(resolve(__dirname, '../static/index.html'), 'utf8');
const state = { chainFilter: 'ALL', positions: new Map() };
const ui = new Map();
function element() { return { children: [], attributes: {}, style: {}, clientWidth: 1000, clientHeight: 650,
  setAttribute(key, value) { this.attributes[key] = value; }, append(...nodes) { this.children.push(...nodes); },
  replaceChildren(...nodes) { this.children = nodes; } }; }
const context = { state, Map, Set, Number, String, Math, URL, friendly: (value) => String(value || '').replaceAll('_', ' '),
  $: (selector) => { if (!ui.has(selector)) ui.set(selector, element()); return ui.get(selector); },
  el: () => element(), svgEl: (tag, attrs) => ({ tag, ...attrs }) };
const chainSource = source.slice(source.indexOf('const chains = {'), source.indexOf('function isCandidateEdge('));
const explorerSource = source.slice(source.indexOf('function explorerLink('), source.indexOf('const livePresets ='));
const caseChains = source.slice(source.indexOf('function caseChains('), source.indexOf('function setChainFilter('));
const transferChecks = source.slice(source.indexOf('function isCandidateEdge('), source.indexOf('function explorerLink('));
const liveChecks = source.slice(source.indexOf('function isLiveRecord('), source.indexOf('const chains ='));
const layout = source.slice(source.indexOf('function visibleGraph('), source.indexOf('function edgeGeometry('));
vm.createContext(context); vm.runInContext(`${chainSource}\n${liveChecks}\n${transferChecks}\n${explorerSource}\n${caseChains}\n${layout}`, context);
const node = (id, hop, type, zone, chain) => ({ id, hop, node_type: type, node_zone: zone, chain, metadata: {} });
state.graph = { nodes: [node('victim', 0, 'VICTIM', 'FIAT_BANK'), node('va', 1, 'PAKD_VA', 'CRYPTO_EXCHANGE_BRIDGE'),
  node('deposit', 2, 'PAKD_FIAT_DEPOSIT', 'CRYPTO_EXCHANGE_BRIDGE'), node('conversion', 3, 'PAKD_CRYPTO_CONVERSION', 'CRYPTO_EXCHANGE_BRIDGE'), node('user', 4, 'PAKD_USER_ACCOUNT', 'CRYPTO_EXCHANGE_BRIDGE'),
  ...['BTC', 'SOLANA', 'ETHEREUM'].flatMap((chain) => [node(`${chain}-initial`, 5, 'INITIAL_WALLET', 'ONCHAIN_WALLET', chain), node(`${chain}-hop`, 6, 'WALLET', 'ONCHAIN_WALLET', chain)]),
  node('sol-bank', 7, 'FINAL_CASHOUT', 'FIAT_BANK') ], governance_requests: [{ target_node_id: 'sol-bank' }],
  chain_dispersals: ['BTC', 'SOLANA', 'ETHEREUM'].map((chain) => ({ chain })), edges: [] };
const edge = (source, target) => ({ id: `${source}:${target}`, source, target });
state.graph.edges.push(edge('victim', 'va'), edge('va', 'deposit'), edge('deposit', 'conversion'), edge('conversion', 'user'), ...['BTC', 'SOLANA', 'ETHEREUM'].flatMap((chain) => [edge('user', `${chain}-initial`), edge(`${chain}-initial`, `${chain}-hop`)]), edge('SOLANA-hop', 'sol-bank'));
const original = JSON.stringify(state.graph);
context.layoutNodes();
assert.equal(state.lanes.length, 3);
assert.equal(state.viewNodes.some((n) => n.id === 'deposit'), false);
assert.ok(state.viewEdges.some((e) => e.source === 'va' && e.target === 'user' && e.metadata.collapsed_api_steps.length === 3));
assert.equal(state.branchByNode.get('sol-bank'), 'SOLANA');
const laneY = ['BTC', 'SOLANA', 'ETHEREUM'].map((chain) => state.positions.get(`${chain}-initial`).y);
assert.ok(laneY[1] - laneY[0] >= 200 && laneY[2] - laneY[1] >= 200);
assert.equal(state.positions.get('BTC-initial').x, state.positions.get('SOLANA-initial').x);
assert.ok(state.positions.get('BTC-hop').x > state.positions.get('BTC-initial').x);
state.chainFilter = 'SOLANA'; context.layoutNodes();
assert.equal(state.lanes.length, 1);
assert.ok(state.viewNodes.some((n) => n.id === 'victim'));
assert.ok(state.viewNodes.some((n) => n.id === 'sol-bank'));
assert.equal(state.viewNodes.some((n) => n.chain === 'BTC' || n.chain === 'ETHEREUM'), false);
assert.equal(JSON.stringify(state.graph), original, 'Filtering must not mutate evidence or governance');
// Governance targets remain inspectable even when their node type is normally summarized.
state.graph.governance_requests.push({ target_node_id: 'deposit' }); context.layoutNodes();
assert.ok(state.viewNodes.some((n) => n.id === 'deposit'));
const btc = context.explorerLink('tx', 'a'.repeat(64), null, { chain: 'BTC' });
assert.equal(btc.href, `https://mempool.space/tx/${'a'.repeat(64)}`);
const sol = context.explorerLink('address', 'So11111111111111111111111111111111111111112', null, { chain: 'SOLANA' });
assert.equal(sol.href, 'https://solscan.io/account/So11111111111111111111111111111111111111112');
assert.equal(context.explorerLink('tx', 'a'.repeat(64), null, { chain: 'BTC', explorer_url: 'https://evil.example/tx/' + 'a'.repeat(64) }), null);
assert.equal(context.explorerLink('address', '0x' + '1'.repeat(40), null, { chain: 'ETHEREUM', explorer_url: 'javascript:alert(1)' }), null);
assert.equal(context.isLiveTransfer({ chain: 'BTC', tx_hash: 'a'.repeat(64), metadata: { is_live: true } }), true);
assert.equal(context.isLiveTransfer({ chain: 'SOLANA', tx_hash: '3'.repeat(88), metadata: { is_live: true } }), true);
assert.equal(context.isLiveTransfer({ chain: 'ETHEREUM', tx_hash: '0x' + 'a'.repeat(64), metadata: { is_live: true, is_candidate: true } }), false);
assert.equal(context.isLiveTransfer({ chain: 'BTC', tx_hash: 'a'.repeat(64), metadata: { simulation: true } }), false);
assert.equal(context.isLiveTransfer({ chain: 'ETHEREUM', tx_hash: '0x' + 'a'.repeat(64), metadata: { evidence_source: 'CRYPTOAPIS_MAINNET' } }), true);
assert.equal(context.scannerLabel('ETHERSCAN_V2,CRYPTOAPIS'), 'Etherscan V2 · Crypto APIs');
assert.ok(source.includes('api("/api/v1/trail/live-scanner"'));
assert.equal((html.match(/data-step="[1-4]"/g) || []).length, 4);
assert.ok(/<details class="report-details">/.test(html), 'Technical FNA form is collapsed initially');
for (const id of ['chain-dispersal-cards', 'chain-filters', 'run-scenario-button', 'sidebar-animate-button', 'mirror-db-button', 'live-chain']) assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
async function checkLiveRequests() {
  const requestSource = source.slice(source.indexOf('async function api('), source.indexOf('function log('));
  const beginSource = source.slice(source.indexOf('function beginTrace('), source.indexOf('function acceptGraph('));
  const acceptSource = source.slice(source.indexOf('function acceptGraph('), source.indexOf('function failTrace('));
  const failSource = source.slice(source.indexOf('function failTrace('), source.indexOf('async function trace()'));
  const traceSource = source.slice(source.indexOf('async function traceLive('), source.indexOf('function isLiveRecord('));
  const hopOptions = html.match(/<select id="live-hops"[^>]*>(.*?)<\/select>/s)[1];
  assert.deepEqual([...hopOptions.matchAll(/value="(\d+)"/g)].map((option) => Number(option[1])), [1, 2, 3, 4, 5, 6]);
  assert.equal(/<input[^>]+(?:name|id)="[^"]*cryptoapis[^"]*key/i.test(html), false, 'Crypto APIs credentials must stay on the server');
  let sent, partialGraph, failure = false;
  Object.assign(context, { JSON, Error,
    fetch: async (path, options) => {
      sent = { path, method: options.method, body: JSON.parse(options.body) };
      const primary = sent.body.source_chain === 'BTC' ? 'MEMPOOL_SPACE' : sent.body.source_chain === 'SOLANA' ? 'SOLANA_MAINNET_RPC' : 'ETHERSCAN_V2';
      return { ok: !failure, json: async () => failure ? { detail: 'Crypto APIs quota exceeded' } : partialGraph || {
        simulation: false, nodes: [], edges: [], live_metadata: {
          provider: sent.body.max_hops > 3 ? primary + ',CRYPTOAPIS' : primary, source_chain: sent.body.source_chain,
          cryptoapis_history_window_days: sent.body.source_chain === 'SOLANA' ? 1 : 14,
          hop_strategy: { initial_max_hops: 3, continuation_provider: 'CRYPTOAPIS', max_hops: sent.body.max_hops },
          requests_made: 9, cryptoapis_requests_made: sent.body.max_hops > 3 ? 3 : 0,
          cryptoapis_mode: 'FREE', cryptoapis_page_size: 1, cryptoapis_request_limit: 6, cryptoapis_credit_limit: 2000,
          cryptoapis_credits_consumed: 500, cryptoapis_credits_estimated: 1000,
          fallback_reason: sent.body.source_chain === 'SOLANA' ? 'SOLSCAN_PLAN_REQUIRED' : undefined,
          activity: [{ signature: '3'.repeat(88), status: 'UNSUPPORTED_INSTRUCTION' }] }
      } };
    },
    validateLiveAddress: () => true,
    stopAnimation() {}, renderCaseSummary() {}, renderMetrics() {}, renderGraph() {}, resetView() {}, selectNode() {},
    renderStepper() {}, renderInspector() {}, renderEvidence() {}, renderPakd() {}, renderActions() {}, toast() {}, log() {} });
  state.request = 0; state.packets = new Map(); state.decisionDrafts = new Map(); state.busyRequests = new Set();
  vm.runInContext(`${requestSource}\n${beginSource}\n${acceptSource}\n${failSource}\n${traceSource}`, context);
  context.$('#live-form').reportValidity = () => true;
  context.$('#live-wallet').value = '  0x' + '1'.repeat(40) + '  ';
  context.$('#live-hops').value = hopOptions.match(/value="(\d+)" selected/)[1]; context.$('#live-token').value = 'ALL';
  context.$('#live-origin').checked = false; context.$('#live-cross-chain').checked = false;
  for (const chain of ['BTC', 'SOLANA', 'ETHEREUM', 'BSC', 'POLYGON', 'ARBITRUM', 'OPTIMISM', 'BASE']) {
    context.$('#live-chain').value = chain;
    context.$('#live-api-key').value = '  evm-test-key  ';
    await context.traceLive();
    assert.equal(sent.path, '/api/v1/trail/live-scanner'); assert.equal(sent.method, 'POST');
    const native = ['BTC', 'SOLANA'].includes(chain);
    assert.deepEqual(Object.keys(sent.body).sort(), ['auto_scan_cross_chain', 'direction', ...(native ? [] : ['etherscan_api_key']), 'max_hops', 'simulate_fna_and_mirror_origin', 'source_chain', 'token_filter', 'wallet_address']);
    assert.equal(sent.body.max_hops, 6); assert.equal(sent.body.source_chain, chain);
    assert.equal(sent.body.etherscan_api_key, native ? undefined : 'evm-test-key');
    assert.equal(context.$('#live-api-key').value, '', 'Optional Etherscan key must be cleared after submission');
    assert.equal(sent.body.wallet_address, '0x' + '1'.repeat(40));
    assert.match(context.$('#live-status').textContent, /Crypto APIs/);
    assert.match(context.$('#live-status').textContent, chain === 'SOLANA' ? /1 hari/ : /14 hari/);
    assert.match(context.$('#live-status').textContent, /6 hop/);
    assert.match(context.$('#live-status').textContent, /hop 1–3/i);
    assert.match(context.$('#live-status').textContent, /hop 4–6/i);
    assert.match(context.$('#live-status').textContent, /Free/i);
    assert.match(context.$('#live-status').textContent, /3\/6 permintaan Crypto APIs/);
    assert.match(context.$('#live-status').textContent, /500 terukur \+ 1000 perkiraan.*2000/);
    assert.match(context.$('#live-status').textContent, chain === 'BTC' ? /Mempool.space/ : chain === 'SOLANA' ? /Solana Mainnet RPC/ : /Etherscan V2/);
    if (chain === 'SOLANA') assert.match(context.$('#live-status').textContent, /RPC publik/);
    assert.equal(context.$('#live-status').textContent.includes('evm-test-key'), false);
    assert.equal(state.graph.live_metadata.activity[0].status, 'UNSUPPORTED_INSTRUCTION');
  }
  context.$('#live-hops').value = '2';
  await context.traceLive();
  assert.equal(sent.body.etherscan_api_key, undefined, 'Empty optional Etherscan key must be omitted');
  assert.match(context.$('#live-status').textContent, /hop 1–2/i);
  assert.doesNotMatch(context.$('#live-status').textContent, /Crypto APIs|14 hari|Free|2000/, 'Primary-only scans must not claim a continuation window or Free budget');
  context.$('#live-hops').value = '6';
  const primaryNodes = [node('seed', 0, 'SEED_WALLET', 'ONCHAIN_WALLET', 'ETHEREUM'), ...[1, 2, 3].map((hop) => node(`primary-hop-${hop}`, hop, 'WALLET', 'ONCHAIN_WALLET', 'ETHEREUM'))];
  const primaryEdges = [1, 2, 3].map((hop) => ({ ...edge(primaryNodes[hop - 1].id, primaryNodes[hop].id), chain: 'ETHEREUM', tx_hash: '0x' + String(hop).repeat(64), metadata: { is_live: true } }));
  partialGraph = { simulation: false, nodes: primaryNodes, edges: primaryEdges, warnings: ['Transaksi publik belum membuktikan atribusi dana laporan.'], live_metadata: {
    provider: 'ETHERSCAN_V2', source_chain: 'ETHEREUM', partial: true, wallet_hops_traced: 3,
    continuation_status: 'RATE_LIMIT', cryptoapis_stop_reason: 'Batas laju Crypto APIs tercapai; data scanner utama tetap tersedia.', cryptoapis_http_status: 429,
    retry_after_seconds: 1.5, requests_made: 5, cryptoapis_requests_made: 2,
    cryptoapis_mode: 'FREE', cryptoapis_page_size: 1, cryptoapis_request_limit: 6, cryptoapis_credit_limit: 2000,
    cryptoapis_credits_consumed: 0, cryptoapis_credits_estimated: 1000, cryptoapis_credits_available: 9000,
    hop_strategy: { initial_max_hops: 3, continuation_provider: 'CRYPTOAPIS', max_hops: 6 }
  } };
  await context.traceLive();
  assert.equal(state.graph, partialGraph, 'HTTP 200 partial results must retain the returned evidence');
  assert.equal(state.graph.simulation, false);
  assert.equal(state.graph.nodes, primaryNodes); assert.equal(state.graph.edges, primaryEdges);
  assert.equal(state.graph.live_metadata.provider, 'ETHERSCAN_V2', 'A limited Crypto APIs request must not claim successful provider data');
  assert.match(context.$('#live-status').textContent, /^Etherscan V2:/);
  assert.match(context.$('#live-status').textContent, /Hasil parsial.*3 dari 6 hop/);
  assert.match(context.$('#live-status').textContent, /Batas laju Crypto APIs/);
  assert.match(context.$('#live-status').textContent, /1.5 detik/);
  assert.match(context.$('#live-status').textContent, /sisa akun teramati 9000/);
  assert.equal(context.$('#warnings').hidden, false);
  assert.match(context.$('#warnings').textContent, /Hasil parsial.*3 dari 6 hop/);
  assert.match(context.$('#warnings').textContent, /Transaksi publik belum membuktikan atribusi/);
  assert.doesNotMatch(context.$('#warnings').textContent, /Penelusuran gagal/);
  failure = true;
  await context.traceLive();
  assert.equal(state.graph, null, 'Failed live requests must clear previous evidence');
  assert.equal(context.$('#nodes').children.length, 0);
  assert.match(context.$('#warnings').textContent, /Tidak ada transaksi demo/);
  assert.match(context.$('#live-status').textContent, /Crypto APIs quota exceeded/);
  assert.equal(context.$('#live-trace-button').disabled, false);
}
checkLiveRequests().then(() => console.log('PASS: chain filtering, evidence preservation, safe explorer links, six-hop hybrid requests, optional Etherscan key hygiene, continuation-only Free budgets, native activity, preserved partial-limit evidence, and empty graph on API failure.')).catch((error) => { console.error(error); process.exitCode = 1; });
