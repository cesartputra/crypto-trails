// Pure Arkham view behavior, including evidence provenance and filter boundaries.
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = { Map, Set, Math, Number, String, JSON, Date };
vm.createContext(context);
vm.runInContext(readFileSync(resolve(__dirname, '../static/forensic_graph.js'), 'utf8') + '\nthis.graphModule = ForensicGraph;', context);
const { visualize, timeline, label, project } = context.graphModule;
assert.equal(typeof visualize, 'function', 'The Arkham projection must be available without D3 or a network');
const plain = value => JSON.parse(JSON.stringify(value));
const node = (id, extra = {}) => ({ id, label: id, node_type: 'LIVE_UNATTRIBUTED_WALLET', chain: 'ETHEREUM', asset: 'ETH', ...extra });
const edge = (id, source, target, extra = {}) => ({ id, source, target, asset: 'ETH', chain: 'ETHEREUM', crypto_amount: 1, ...extra });
const seed = node('seed', { metadata: { is_seed_wallet: true } });
const records = view => view.edges.flatMap(bundle => bundle.members);
const ids = view => plain(records(view).map(record => record.id).sort());
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }

// A missing valuation must never become a fabricated USD price or disappear at a numeric boundary.
const moneyGraph = freeze({ nodes: [seed, node('unknown'), node('micro'), node('boundary'), node('metadata'), node('zero'), node('invalid')], edges: [
  edge('unknown', 'seed', 'unknown'), edge('micro', 'seed', 'micro', { amount_usd: .09 }),
  edge('boundary', 'seed', 'boundary', { amount_usd: .10 }),
  edge('metadata', 'seed', 'metadata', { crypto_amount: 0, metadata: { value_usd: .10 } }),
  edge('zero', 'seed', 'zero', { amount_usd: 0 }), edge('invalid', 'seed', 'invalid', { amount_usd: -4 })
] });
const moneyBefore = JSON.stringify(moneyGraph), money = visualize(moneyGraph);
assert.deepEqual(ids(money), ['boundary', 'invalid', 'metadata', 'unknown']);
assert.equal(money.unknownUsdCount, 2);
assert.equal(money.hiddenCount, 2);
assert.equal(money.edges.find(bundle => bundle.target === 'unknown').amount_usd, undefined);
assert.equal(money.edges.find(bundle => bundle.target === 'metadata').amount_usd, .10);
assert.equal(records(money).find(record => record.id === 'unknown'), moneyGraph.edges[0]);
assert.equal(JSON.stringify(moneyGraph), moneyBefore);
assert.deepEqual(ids(visualize(moneyGraph, { focus: false })), ['boundary', 'invalid', 'metadata', 'micro', 'unknown', 'zero']);
const dust = { nodes: [seed, node('big'), node('dust'), node('spam'), node('dex', { node_type: 'DEX_ROUTER' })], edges: [
  edge('big', 'seed', 'big'), edge('dust', 'seed', 'dust', { crypto_amount: .00001 }),
  edge('spam', 'seed', 'spam', { asset: 'SPAM' }), edge('dex', 'seed', 'dex', { asset: 'OTHER', crypto_amount: .0000001 })
] };
assert.deepEqual(ids(visualize(dust)), ['big', 'dex']);

// Filters use evidence dates inclusively; numeric epoch zero is a valid recorded date.
const dated = { nodes: [seed, node('a'), node('b'), node('c'), node('d')], edges: [
  edge('epoch', 'seed', 'a', { timestamp: 0 }), edge('end', 'seed', 'b', { timestamp: '1970-01-01T00:00:01Z' }),
  edge('later', 'seed', 'c', { timestamp: '1970-01-01T00:00:02Z' }), edge('unknown', 'seed', 'd', { timestamp: 'invalid' })
] };
assert.deepEqual(ids(visualize(dated, { range: [0, 1000] })), ['end', 'epoch']);
assert.deepEqual(plain(visualize(dated, { range: [3000, 4000] }).nodes.map(item => item.id)), ['seed']);
assert.deepEqual(plain(records(visualize(dated, { sortTime: true } )).map(item => item.id)), ['epoch', 'end', 'later', 'unknown']);

// Ancestors and descendants include every directed hop, terminate on cycles, and discard unrelated components.
const trail = freeze({ nodes: [seed, ...['up', 'up2', 'down', 'down2', 'elsewhere', 'elsewhere2'].map(id => node(id))], edges: [
  edge('up2-up', 'up2', 'up'), edge('up-seed', 'up', 'seed'), edge('seed-down', 'seed', 'down'),
  edge('down-down2', 'down', 'down2'), edge('down2-down', 'down2', 'down'), edge('unrelated', 'elsewhere', 'elsewhere2')
] });
assert.deepEqual(ids(visualize(trail, { flow: 'IN' })), ['up-seed', 'up2-up']);
assert.deepEqual(ids(visualize(trail, { flow: 'OUT' })), ['down-down2', 'down2-down', 'seed-down']);
assert.deepEqual(plain(visualize(trail, { flow: 'IN' }).nodes.map(item => item.id).sort()), ['seed', 'up', 'up2']);
const pivot = visualize(trail, { focalId: 'down', flow: 'IN' });
assert.equal(pivot.focalId, 'down'); assert.equal(pivot.nodes.find(item => item.id === 'down'), trail.nodes[3]);
assert.equal(pivot.hops.get('down'), 0); assert.equal(pivot.hops.get('up2'), 3);
assert.ok(ids(pivot).includes('seed-down'));
assert.equal(visualize({ nodes: [node('first'), node('split', { node_type: 'PAKD_USER_ACCOUNT' }), node('x'), node('y')], edges: [edge('s-x', 'split', 'x'), edge('s-y', 'split', 'y')] }).focalId, 'split');
assert.equal(visualize({ nodes: [node('first'), seed], edges: [] }).focalId, 'seed');
assert.equal(visualize({ nodes: [node('z-first'), node('a-second')], edges: [] }).focalId, 'z-first');
const sourceFlag = node('account', { node_type: 'PAKD_USER_ACCOUNT', metadata: { is_initial_wallet: true } });
assert.equal(visualize({ nodes: [sourceFlag, seed], edges: [] }).focalId, 'seed', 'An off-chain account flag must not replace an actual seed wallet');

// Chain mode preserves addresses; owner mode requires evidence and preserves every original record and member.
const ownerNodes = [seed,
  node('unknown1', { label: 'Binance', entity_label: 'Binance' }), node('unknown2', { label: 'Binance', entity_label: 'Binance' }),
  node('owned1', { owner_id: 'owner-a' }), node('owned2', { owner_id: 'owner-a' }),
  node('owned-sol', { owner_id: 'owner-a', chain: 'SOLANA' }),
  node('known1', { metadata: { public_entity: { kind: 'PUBLIC_EXCHANGE_WALLET', label: 'Binance' } } }),
  node('known2', { metadata: { public_entity: { kind: 'PUBLIC_EXCHANGE_WALLET', label: 'Binance' } } })
];
const ownerGraph = freeze({ nodes: ownerNodes, edges: ownerNodes.slice(1).map((item, index) => edge(`e${index}`, 'seed', item.id, { crypto_amount: index + 1, ...(index === 3 ? { asset: 'USDT', contract: '0xAAA' } : {}) })) });
assert.equal(visualize(ownerGraph, { group: 'CHAIN' }).nodes.length, 8);
const owners = visualize(ownerGraph, { group: 'OWNER' }), groups = owners.nodes.filter(item => item.cluster_members);
assert.equal(groups.length, 2); assert.equal(owners.hiddenCount, 0);
assert.ok(owners.nodes.some(item => item.id === 'unknown1') && owners.nodes.some(item => item.id === 'unknown2'));
assert.ok(owners.nodes.some(item => item.id === 'owned-sol'));
assert.ok(groups.every(item => item.id.startsWith('owner:')));
const owned = groups.find(item => item.cluster_members.some(member => member.id === 'owned1'));
assert.equal(owned.cluster_members[0], ownerGraph.nodes[3]);
const ownerBundle = owners.edges.find(item => item.target === owned.id);
assert.equal(ownerBundle.members.length, 2); assert.equal(ownerBundle.members[0], ownerGraph.edges[2]);
assert.deepEqual(plain(ownerBundle.totals.map(item => [item.asset, item.amount])), [['ETH', 3], ['USDT', 4]]);
const ownerPivot = visualize(ownerGraph, { group: 'OWNER', focalId: 'known1' });
assert.equal(ownerPivot.nodes.find(item => item.id === 'known1'), ownerGraph.nodes[6]);
assert.ok(!ownerPivot.nodes.some(item => item.cluster_members?.some(member => member.id === 'known1')));
assert.deepEqual(plain(owners.nodes.map(item => item.id)), plain(visualize({ ...ownerGraph, nodes: [...ownerGraph.nodes].reverse(), edges: [...ownerGraph.edges].reverse() }, { group: 'OWNER' }).nodes.map(item => item.id)));
const many = { nodes: [seed, ...Array.from({ length: 12 }, (_, index) => node(`wallet${index}`))], edges: Array.from({ length: 12 }, (_, index) => edge(`many${index}`, 'seed', `wallet${index}`)) };
assert.equal(visualize(many).nodes.length, 13, 'Arkham must not silently apply the old eight-wallet cap');
assert.equal(project(many).clusterCount, 1, 'The audit view retains its existing cap');

// Histogram height counts evidence records, never token quantity or an invented current date.
const histogram = timeline([edge('one', 'a', 'b', { timestamp: 0, crypto_amount: 999999 }), { members: [edge('two', 'a', 'b', { timestamp: 1000 }), edge('three', 'a', 'b', { timestamp: 2000 })] }, edge('bad', 'a', 'b', { timestamp: 'invalid' }), edge('missing', 'a', 'b')], 2);
assert.equal(histogram.start, 0); assert.equal(histogram.end, 2000);
assert.deepEqual(plain(histogram.bins.map(bin => bin.count)), [1, 2]); assert.equal(histogram.undatedCount, 2);
assert.deepEqual(plain(timeline([])), { start: null, end: null, bins: [], undatedCount: 0 });
assert.deepEqual(plain(timeline([edge('missing', 'a', 'b')])), { start: null, end: null, bins: [], undatedCount: 1 });
const single = timeline([edge('epoch', 'a', 'b', { timestamp: 0 })]);
assert.equal(single.end - single.start, 86400000); assert.equal(single.bins.reduce((sum, bin) => sum + bin.count, 0), 1);
assert.equal(label(ownerGraph.nodes[6]), 'binance');
assert.equal(label(node('0x12345678901234567890', { label: 'Binance', entity_label: 'Binance' })), '0x12345...7890');
assert.ok(label(node('account', { node_type: 'PAKD_USER_ACCOUNT', entity_label: 'Indodax' })).includes('indodax'));
assert.equal(label(node('bank-record', { node_type: 'VICTIM', node_zone: 'FIAT_BANK', label: 'Bank BCA' })), 'bank bca');
assert.equal(label(node('database-id', { label: '0x1234567890123456789012345678901234567890' })), '0x12345...7890');
assert.equal(JSON.stringify(moneyGraph), moneyBefore);
console.log('PASS: Arkham focal pivots, USD evidence, directed filters, owner provenance, full chain addresses and transaction activity timeline.');
