// Original evidence and audit projection: node tests/frontend_forensic_workspace_selfcheck.cjs
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function graphMemberEdges(');
assert.ok(start >= 0, 'Display bundles must resolve original evidence');
const raw = [
  { id: 'fiat', source: 'bank', target: 'seed', amount_idr: 100 },
  { id: 'eth1', source: 'seed', target: 'hop', asset: 'ETH', crypto_amount: 1 },
  { id: 'eth2', source: 'seed', target: 'hop', asset: 'ETH', crypto_amount: .2 },
  { id: 'spam', source: 'seed', target: 'hop', asset: 'PEPE', crypto_amount: 9000000 },
  { id: 'target', source: 'hop', target: 'exchange', asset: 'ETH', crypto_amount: 1.1 }
];
const nodes = [{id:'bank',node_zone:'FIAT_BANK'}, {id:'seed',metadata:{is_seed_wallet:true}}, {id:'hop'}, {id:'exchange',node_type:'PUBLIC_EXCHANGE_WALLET'}];
const context = { Map, Set, Number, String, state: {graph:{nodes, edges:raw}},
  transactionRows: graph => graph.edges };
vm.createContext(context);
vm.runInContext(readFileSync(resolve(__dirname, '../static/forensic_graph.js'), 'utf8'), context);
vm.runInContext(source.slice(start, source.indexOf('function renderTransferItems(', start)), context);
const bundle = {members:[raw[1],raw[2],{id:'summary',metadata:{collapsed_api_steps:['fiat','eth1']}}]};
const before = JSON.stringify(context.state.graph);
assert.deepEqual(Array.from(context.graphMemberEdges(bundle), e=>e.id), ['eth1','eth2','fiat']);
assert.equal(typeof context.nodeInflowEdges, 'function', 'Card inflow must use only original incoming edges');
assert.deepEqual(Array.from(context.nodeInflowEdges(nodes[2]),e=>e.id), ['eth1','eth2','spam']);
const audit = context.auditTrailRows(context.state.graph);
assert.equal(audit.length, 5, 'Audit includes hidden spam and every original scoped edge');
assert.equal(audit.find(row=>row.edge.id==='target').attributed, true);
assert.equal(audit.find(row=>row.edge.id==='target').hop, 1);
assert.equal(audit.find(row=>row.edge.id==='eth1').hop, 0);
assert.equal(JSON.stringify(context.state.graph), before);
const nonExchange = {nodes:[...nodes,{id:'cashout',node_type:'FINAL_CASHOUT',node_zone:'FIAT_BANK'},{id:'issuer',node_type:'UNHOSTED_WALLET'}],
  edges:[{id:'cash',source:'hop',target:'cashout'},{id:'issuer-review',source:'hop',target:'issuer'}],governance_requests:[{target_node_id:'issuer'}]};
assert.ok(context.auditTrailRows(nonExchange).every(row=>!row.attributed), 'Banks and issuer-review wallets must never be labelled as exchanges');
const originLate = {nodes:[...nodes,{id:'pakd-source',node_type:'PAKD_USER_ACCOUNT',hop:5}],edges:[{id:'chain-start',source:'seed',target:'hop'},{id:'source-funding',source:'pakd-source',target:'seed'}]};
assert.equal(context.auditTrailRows(originLate)[0].edge.id,'source-funding','All origin funding must precede the on-chain hop groups');
context.state.baseWidth = 1130;
vm.runInContext(source.slice(source.indexOf('function edgeGeometry('),source.indexOf('function parallelEdgeSlots(')),context);
const terminalLoop = context.edgeGeometry({x:985,y:128},{x:985,y:128});
assert.ok(terminalLoop.labelX + 98 <= 1130, 'Terminal self-loop pills must stay inside the fitted graph width');
const sameRow = context.edgeGeometry({x:145,y:128},{x:425,y:128});
assert.ok(sameRow.labelY < 86, 'Same-row arrow labels must sit above card content rather than covering titles');
assert.equal(typeof context.placeEdgeLabel,'function','Transfer pills must avoid card and label collisions');
context.state.baseHeight = 900;
const occupied = [{x:250,y:100,width:210,height:84}];
const firstPill = context.placeEdgeLabel({labelX:350,labelY:130},140,occupied);
const secondPill = context.placeEdgeLabel({labelX:350,labelY:130},140,occupied);
assert.ok(firstPill.labelY<87 || firstPill.labelY>197,'Pill cannot cover a card');
assert.ok(Math.abs(firstPill.labelY-secondPill.labelY)>=26,'Pills sharing a crossing must remain separate');
const packed = context.placeEdgeLabel({labelX:350,labelY:130},140,[{x:0,y:0,width:1130,height:900}]);
assert.ok(packed.labelY>900 && context.state.baseHeight>packed.labelY+13,'Dense labels extend the scrollable canvas instead of overlapping');
console.log('PASS: original bundle evidence, complete audit scope, hop grouping and immutable evidence.');
