// Browser-free dashboard filtering regression: node tests/frontend_dashboard_selfcheck.cjs
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function timeFilteredEdges(');
assert.ok(start >= 0, 'Dashboard must filter actual transaction evidence');
const helper = source.slice(start, source.indexOf('function renderTransactions(', start));
const context = { Map, String, chainName: value => String(value || '').replace('_ERC20', '') };
vm.createContext(context); vm.runInContext(helper, context);
vm.runInContext(source.slice(source.indexOf('function visibleGraph('), source.indexOf('function layoutNodes(')), context);
const riskStart = source.indexOf('function riskAssessment(');
assert.ok(riskStart >= 0, 'Risk display must distinguish fixture scores and unassessed live evidence');
vm.runInContext(source.slice(riskStart, source.indexOf('function renderRiskAssessment(', riskStart)), context);
const graph = { nodes: [
  { id: 'a', address: '0xAAAA', label: '<img src=x onerror=alert(1)>', chain: 'ETHEREUM' },
  { id: 'b', address: '0xBBBB', chain: 'ETHEREUM' },
  { id: 'c', address: 'bc1sample', chain: 'BTC' },
  { id: 'bank', account: '00112233' }
], edges: [
  { id: 'eth', source: 'a', target: 'b', tx_hash: '0x123ABC', chain: 'ETHEREUM_ERC20', asset: 'USDT' },
  { id: 'btc', source: 'c', target: 'bank', tx_hash: 'abc456', chain: 'BTC' },
  { id: 'fiat', source: 'bank', target: 'bank', label: 'Fiat handoff' }
] };
const original = JSON.stringify(graph);
const ids = (...args) => Array.from(context.transactionRows(graph, ...args), edge => edge.id);
assert.deepEqual(ids('ALL'), ['eth', 'btc', 'fiat']);
assert.deepEqual(ids('ETHEREUM'), ['eth', 'fiat']);
assert.deepEqual(ids('BTC'), ['btc', 'fiat']);
assert.deepEqual(ids('ALL', '  123abc '), ['eth']);
assert.deepEqual(ids('ALL', '0xBBBB'), ['eth']);
assert.deepEqual(ids('ALL', '00112233'), ['btc', 'fiat']);
assert.deepEqual(ids('ALL', '<img'), ['eth']);
assert.deepEqual(ids('ETHEREUM', 'bc1sample'), []);
assert.deepEqual(ids('ALL', 'unknown'), []);
assert.equal(JSON.stringify(graph), original, 'Searching must not mutate evidence');
const now = Date.parse('2026-10-24T12:00:00Z'), day = 86400000;
const dated = { simulation: false, nodes: [], edges: [
  { id: 'latest', timestamp: new Date(now).toISOString() },
  { id: 'boundary', timestamp: new Date(now - 7 * day).toISOString() },
  { id: 'old', timestamp: new Date(now - 7 * day - 1).toISOString() },
  { id: 'future', timestamp: new Date(now + 1).toISOString() },
  { id: 'missing' }, { id: 'invalid', timestamp: 'invalid' }
] };
const datesBefore = JSON.stringify(dated);
assert.deepEqual(Array.from(context.timeFilteredEdges(dated, '7', now), edge => edge.id), ['latest', 'boundary']);
assert.equal(context.timeFilteredEdges(dated, 'ALL', now).length, 6);
const synthetic = { simulation: true, nodes: [], edges: [
  { id: 'fixture-latest', timestamp: '2024-01-30T00:00:00Z' },
  { id: 'fixture-in-range', timestamp: '2024-01-23T00:00:00Z' },
  { id: 'fixture-old', timestamp: '2024-01-22T23:59:59Z' }
] };
assert.deepEqual(Array.from(context.timeFilteredEdges(synthetic, '7', now), edge => edge.id), ['fixture-latest', 'fixture-in-range']);
assert.equal(JSON.stringify(dated), datesBefore, 'Range filtering must not mutate evidence');
assert.equal(context.riskAssessment({ simulation: true }, { taint_score_pct: '92' }).tier, 'Tinggi');
assert.equal(context.riskAssessment({ simulation: true }, { taint_score_pct: 0 }).score, 0);
assert.equal(context.riskAssessment({ simulation: true }, { taint_score_pct: null }).score, null);
assert.equal(context.riskAssessment({ simulation: true }, { taint_score_pct: 101 }).score, null);
assert.equal(context.riskAssessment({ simulation: true }, { taint_score_pct: false }).score, null);
assert.equal(context.riskAssessment({ simulation: false }, { taint_score_pct: 92 }).tier, 'Belum dinilai');
const branched = { simulation: true, nodes: [
  { id: 'btc-wallet', node_zone: 'ONCHAIN_WALLET', chain: 'BTC' },
  { id: 'btc-p2p', node_zone: 'CRYPTO_EXCHANGE_BRIDGE' },
  { id: 'btc-bank', node_zone: 'FIAT_BANK' },
  { id: 'eth-wallet', node_zone: 'ONCHAIN_WALLET', chain: 'ETHEREUM' },
  { id: 'eth-recipient', node_zone: 'ONCHAIN_WALLET', chain: 'ETHEREUM' }
], edges: [
  { id: 'btc-old', source: 'btc-wallet', target: 'btc-p2p', timestamp: '2024-01-01T00:00:00Z' },
  { id: 'btc-cashout', source: 'btc-p2p', target: 'btc-bank', timestamp: '2024-01-30T00:00:00Z' },
  { id: 'eth-recent', source: 'eth-wallet', target: 'eth-recipient', timestamp: '2024-01-30T00:00:00Z' }
] };
const branchedBefore = JSON.stringify(branched);
context.state = { graph: branched, chainFilter: 'ETHEREUM', timeRange: '7' };
const scoped = context.visibleGraph();
assert.deepEqual(Array.from(scoped.nodes, node => node.id), ['eth-wallet', 'eth-recipient'], 'Date filtering must preserve branch identity from older parent evidence');
assert.deepEqual(Array.from(context.transactionRows(branched, 'ETHEREUM', '', '7'), edge => edge.id), ['eth-recent'], 'Fiat cash-out retains the full trace blockchain branch in the table');
assert.equal(JSON.stringify(branched), branchedBefore, 'Branch projection must not mutate evidence');
context.state = {graph:{simulation:true,nodes:[{id:'seed',metadata:{is_seed_wallet:true}},{id:'recent'},{id:'hidden-pakd',node_type:'PAKD_FIAT_DEPOSIT'}],edges:[{id:'recent',source:'seed',target:'recent',timestamp:'2024-01-30T00:00:00Z'},{id:'old',source:'hidden-pakd',target:'seed',timestamp:'2024-01-01T00:00:00Z'}]},chainFilter:'ALL',timeRange:'7',focalId:'hidden-pakd'};
assert.ok(context.visibleGraph().nodes.some(node=>node.id==='hidden-pakd'),'A searched focal remains visible even when its records fall outside the active time range');
assert.ok(source.includes('matches.slice(0, 50)'), 'Transaction table must keep its visible row cap');
console.log('PASS: transaction search, chain/time scopes, date boundaries, fixture-only risk indicators and immutable evidence.');
