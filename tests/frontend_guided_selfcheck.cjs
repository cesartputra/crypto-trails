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
const context = { state, Map, Set, Number, String, Math, URL,
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
assert.ok(source.includes('api("/api/v1/trail/live-scanner"'));
assert.equal((html.match(/data-step="[1-4]"/g) || []).length, 4);
assert.ok(/<details class="report-details">/.test(html), 'Technical FNA form is collapsed initially');
for (const id of ['chain-dispersal-cards', 'chain-filters', 'run-scenario-button', 'sidebar-animate-button', 'mirror-db-button', 'live-chain']) assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
console.log('PASS: chain filtering, shared origin, swimlanes, evidence preservation, governance targets, safe BTC/Solana explorer links, guided controls, native transfer evidence classification.');
