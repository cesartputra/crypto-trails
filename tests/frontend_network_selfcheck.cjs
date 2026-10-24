// Pure forensic projection and deterministic four-column layout: node tests/frontend_network_selfcheck.cjs
const { readFileSync, existsSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const modulePath = resolve(__dirname, '../static/forensic_graph.js');
assert.ok(existsSync(modulePath), 'The forensic graph projection module must exist');
const context = { Map, Set, Math, Number, String, JSON };
vm.createContext(context);
vm.runInContext(readFileSync(modulePath, 'utf8') + '\nthis.graphModule = ForensicGraph;', context);
const { project, layout, role, amountSummary, isExchange } = context.graphModule;
const plain = value => JSON.parse(JSON.stringify(value));
const node = (id, extra = {}) => ({ id, label: id, node_zone: 'ONCHAIN_WALLET', node_type: 'LIVE_UNATTRIBUTED_WALLET', chain: 'ETHEREUM', asset: 'ETH', ...extra });
const edge = (id, source, target, amount = 1, extra = {}) => ({ id, source, target, asset: 'ETH', crypto_amount: amount, chain: 'ETHEREUM', ...extra });
const seed = node('seed', { metadata: { is_seed_wallet: true } });
const wallet = node('wallet');
const frozen = graph => {
  function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } }
  freeze(graph); return graph;
};

// Wrong role rules would hide a known target or present an untrusted wallet label as attribution.
assert.equal(role(node('s', { node_type: 'PAKD_USER_ACCOUNT', entity_label: 'Indodax', metadata: { is_initial_wallet: true } })), 'source');
assert.equal(role(node('fiat', { node_zone: 'FIAT_BANK' })), 'source');
assert.equal(role(node('va', { node_type: 'PAKD_VA', entity_label: 'Tokocrypto' })), 'source');
assert.equal(role(seed), 'seed');
assert.equal(role(node('bridge', { node_type: 'BRIDGE_PROTOCOL' })), 'bridge');
assert.equal(role(node('dex', { node_type: 'DEX_ROUTER' })), 'bridge');
assert.equal(role(node('public', { metadata: { public_entity: { kind: 'PUBLIC_EXCHANGE_WALLET', label: 'Binance 14' } } })), 'target');
for (const entity_label of ['Binance 14', 'Indodax', 'Tokocrypto', 'Pintu', 'Reku']) {
  assert.equal(role(node(entity_label, { node_type: 'PUBLIC_EXCHANGE_WALLET', entity_label })), 'target');
  assert.equal(role(node(entity_label, { entity_label })), 'wallet', 'Free-text entity labels do not establish attribution');
}
for (const metadata of [{ public_entity: { kind: 'DEX_ROUTER', label: 'Uniswap V3' } }, { entity_kind: 'CROSS_CHAIN_BRIDGE' }, { entity_type: 'DEX_ROUTER' }]) assert.equal(role(node('structured-router', { metadata })), 'bridge');
for (const node_type of ['LOCAL_EXCHANGE_DEPOSIT', 'GLOBAL_EXCHANGE_DEPOSIT', 'PAKD_MIRROR_MATCH', 'P2P_ESCROW', 'PAKD_RECEIVER_ACCOUNT', 'CUSTODIAL_WALLET']) assert.equal(role(node(node_type, { node_type })), 'target');
assert.equal(role(node('unknown', { entity_label: 'Exchange unknown', label: 'Binance 14' })), 'wallet');
assert.equal(role(node('<script>globalThis.pwned = true</script>', { label: '<img src=x onerror=alert(1)>Binance 14' })), 'wallet');
assert.equal(context.pwned, undefined);
assert.equal(typeof isExchange, 'function', 'Actionable targets need a separate exchange-attribution predicate');
assert.equal(isExchange(node('exchange', { node_type: 'PUBLIC_EXCHANGE_WALLET' })), true);
assert.equal(isExchange(node('public', { metadata: { public_entity: { kind: 'PUBLIC_EXCHANGE_WALLET', label: 'Binance 14' } } })), true);
assert.equal(isExchange(node('bank', { node_type: 'FINAL_CASHOUT', node_zone: 'FIAT_BANK' })), false);
assert.equal(isExchange(node('cold', { metadata: { recovery_institution: 'ISSUER' } })), false);
assert.equal(isExchange(node('unknown-brand', { entity_label: 'Binance 14' })), false);
assert.equal(role(node('bank', { node_type: 'FINAL_CASHOUT', node_zone: 'FIAT_BANK' })), 'target');
assert.equal(role(node('cold', { metadata: { recovery_institution: 'ISSUER' } })), 'target');

// Parallel records stay itemized, direction stays separate, and token symbols never merge contracts/chains.
const bundleGraph = frozen({ nodes: [seed, wallet], edges: [
  edge('eth1', 'seed', 'wallet', 1), edge('eth2', 'seed', 'wallet', '.25'),
  edge('pepe', 'seed', 'wallet', 245052980, { asset: 'PEPE', metadata: { contract_address: '0xPepe' } }),
  edge('zero', 'seed', 'wallet', 0), edge('bad', 'seed', 'wallet', 'Infinity'),
  edge('token1', 'seed', 'wallet', 3, { asset: 'USDT', metadata: { contract_address: '0xaaa' } }),
  edge('token2', 'seed', 'wallet', 4, { asset: 'USDT', metadata: { contract_address: '0xbbb' } }),
  edge('token3', 'seed', 'wallet', 5, { asset: 'USDT', chain: 'TRON', metadata: { contract_address: '0xaaa' } }),
  edge('reverse', 'wallet', 'seed', 2)
] });
const before = JSON.stringify(bundleGraph), bundled = project(bundleGraph);
assert.equal(bundled.edges.length, 2);
assert.equal(bundled.roles.size, 2);
assert.ok(bundled.roles.has('seed') && bundled.roles.has('wallet'));
const main = bundled.edges.find(item => item.source === 'seed');
assert.equal(main.members.length, 8, 'Even hidden spam/zero line items must remain inspectable in a visible bundle');
assert.equal(main.members.find(item => item.id === 'pepe'), bundleGraph.edges[2]);
assert.equal(main.asset, 'ETH'); assert.equal(main.crypto_amount, 1.25);
assert.equal(main.totals.filter(item => item.asset === 'USDT').length, 3);
assert.ok(main.totals.every(item => Number.isFinite(item.amount) && item.amount > 0));
assert.ok(amountSummary(main).includes('1.25 ETH'));
assert.ok(amountSummary(main).includes('8 tx'));
assert.ok(!amountSummary(main).includes('USD'), 'Token units must never be presented as a USD valuation');
assert.equal(JSON.stringify(bundleGraph), before, 'Projection must not mutate evidence, even when inputs are frozen');
const reversed = project({ ...bundleGraph, nodes: [...bundleGraph.nodes].reverse(), edges: [...bundleGraph.edges].reverse() });
assert.deepEqual(plain(bundled.edges), plain(reversed.edges), 'Bundle IDs, itemization and totals must be independent of input order');

// Candidate correlation must survive aggregation and never become a confirmed critical freeze path.
const candidateView = project({ nodes: [seed, node('target', { node_type: 'PUBLIC_EXCHANGE_WALLET', entity_label: 'Binance 14' })], edges: [
  edge('observed', 'seed', 'target', 10), edge('candidate', 'seed', 'target', 10, { metadata: { is_candidate: true } })
] });
assert.equal(candidateView.edges[0].candidate, true);
assert.equal(candidateView.edges[0].metadata.is_candidate, true);
assert.equal(candidateView.edges[0].critical, false);
assert.equal(candidateView.roles.get('target'), 'target');
const liveCandidate = project({ simulation: false, nodes: [seed, wallet], edges: [edge('bridge', 'seed', 'wallet', 1, { edge_type: 'CROSS_CHAIN_BRIDGE', metadata: { evidence_source: 'CRYPTOAPIS_MAINNET' } })] });
assert.equal(liveCandidate.edges[0].candidate, true);
assert.equal(liveCandidate.edges[0].critical, false);
const governance = project({ nodes: [seed, node('request-target')], edges: [], governance_requests: [{ target_node_id: 'request-target', institution_category: 'PAKD' }] });
assert.equal(governance.roles.get('request-target'), 'target');
assert.ok(governance.nodes.some(item => item.id === 'request-target'));

// A numeric zero is hidden in focus; fiat-only provenance remains, and disabling focus restores evidence.
const dustGraph = { nodes: [seed, wallet, node('spam'), node('zero'), node('target', { node_type: 'LOCAL_EXCHANGE_DEPOSIT' }), node('bridge', { node_type: 'DEX_ROUTER' }), node('origin', { node_zone: 'FIAT_BANK' })], edges: [
  edge('small', 'seed', 'wallet', .0002), edge('spam', 'seed', 'spam', 800000, { asset: 'ASTEROID' }), edge('zero', 'seed', 'zero', 0),
  edge('fiat', 'origin', 'seed', 0, { amount_idr: 1000000, asset: null })
] };
const focused = project(dustGraph);
assert.deepEqual(plain(focused.edges.flatMap(item => item.members.map(member => member.id)).sort()), ['fiat', 'small']);
for (const id of ['seed', 'origin', 'target', 'bridge']) assert.ok(focused.nodes.some(item => item.id === id));
assert.ok(focused.hiddenEdges.some(item => item.id === 'spam'));
const full = project(dustGraph, { focus: false });
assert.equal(full.nodes.length, 7); assert.equal(full.edges.length, 4);
assert.equal(full.clusterCount, 0);
// Relative dust is scoped to the same asset, chain and contract, and never hides attributed endpoints.
const relativeDust = { nodes: [seed, node('big'), node('micro'), node('exchange', { node_type: 'PUBLIC_EXCHANGE_WALLET' }), node('bridge', { node_type: 'DEX_ROUTER' }), node('other-asset'), node('other-contract'), node('other-chain')], edges: [
  edge('main-value', 'seed', 'big', 1), edge('dust-value', 'seed', 'micro', .0002),
  edge('tiny-exchange', 'seed', 'exchange', .00001), edge('tiny-bridge', 'bridge', 'seed', .00001), edge('zero-exchange', 'seed', 'exchange', 0),
  edge('independent-asset', 'seed', 'other-asset', .000001, { asset: 'USDC' }),
  edge('independent-contract', 'seed', 'other-contract', .000001, { metadata: { contract_address: '0xOther' } }),
  edge('independent-chain', 'seed', 'other-chain', .000001, { chain: 'SOLANA' })
] };
const relativeFocus = project(relativeDust);
assert.ok(!relativeFocus.nodes.some(item => item.id === 'micro'), 'Ordinary wallet dust below 0.1% of the largest comparable transfer should be hidden');
assert.ok(relativeFocus.hiddenEdges.some(item => item.id === 'dust-value'));
for (const id of ['tiny-exchange', 'tiny-bridge', 'independent-asset', 'independent-contract', 'independent-chain']) assert.ok(relativeFocus.edges.some(item => item.members.some(member => member.id === id)), id);
assert.ok(relativeFocus.hiddenEdges.some(item => item.id === 'zero-exchange'));
assert.equal(project(relativeDust, { focus: false }).nodes.length, 8);
assert.ok(project(relativeDust, { focus: false }).edges.some(item => item.members.some(member => member.id === 'dust-value')));
const pairedDust = project({ nodes: [seed, wallet], edges: [edge('main-pair', 'seed', 'wallet', 1), edge('dust-pair', 'seed', 'wallet', .0002)] });
assert.equal(pairedDust.edges[0].members.length, 2, 'Dust stays itemized when another material transfer makes its pair visible');

for (const crypto_amount of [NaN, Infinity, -1, true, '', null]) {
  const invalid = project({ nodes: [seed, wallet], edges: [edge('invalid', 'seed', 'wallet', crypto_amount)] });
  assert.equal(invalid.edges.length, 0, `Invalid quantity ${crypto_amount} must not create a money flow`);
}
for (const live_metadata of [{ token_filter: 'PEPE' }, {}]) {
  const pepeSeed = node('seed', { asset: 'PEPE', metadata: { is_seed_wallet: true } });
  assert.equal(project({ nodes: [pepeSeed, wallet], edges: [edge('primary', 'seed', 'wallet', 245052980, { asset: 'PEPE' })], live_metadata }).edges.length, 1, 'Explicit primary meme-token investigations must remain visible');
}
assert.equal(project({ nodes: [seed, wallet], edges: [edge('verified', 'seed', 'wallet', 1, { asset: 'OTHER', metadata: { token_verified: true } })] }).edges.length, 1);

// The documented 36-node / 52-record trail collapses to readable bundles while protecting every attributed node.
const thirty = Array.from({ length: 30 }, (_, i) => node(`w${String(i).padStart(2, '0')}`));
const large = frozen({ nodes: [node('origin', { node_zone: 'FIAT_BANK' }), node('pakd', { node_type: 'PAKD_USER_ACCOUNT', entity_label: 'Indodax' }), seed,
  node('bridge', { node_type: 'BRIDGE_PROTOCOL' }), node('target', { node_type: 'PUBLIC_EXCHANGE_WALLET', entity_label: 'Binance 14' }), node('target2', { node_type: 'LOCAL_EXCHANGE_DEPOSIT' }), ...thirty], edges: [
  ...thirty.map((item, i) => edge(`to-${item.id}`, 'seed', item.id, i + 1)),
  ...Array.from({ length: 18 }, (_, i) => edge(`parallel-${i}`, 'seed', 'w00', i + 1)),
  edge('fiat', 'origin', 'pakd', 0, { amount_idr: 50000000 }), edge('withdrawal', 'pakd', 'seed', 100), edge('to-bridge', 'w00', 'bridge', 1), edge('to-target', 'bridge', 'target', 1)
] });
assert.equal(large.nodes.length, 36); assert.equal(large.edges.length, 52);
const crowded = project(large), cluster = crowded.nodes.find(item => item.cluster_members);
assert.equal(crowded.clusterCount, 1); assert.equal(cluster.cluster_members.length, 22);
assert.equal(crowded.hiddenCount, 22);
assert.equal(crowded.nodes.filter(item => crowded.columns.get(item.id) === 2 && crowded.roles.get(item.id) === 'wallet').length, 8);
for (const id of ['origin', 'pakd', 'seed', 'bridge', 'target', 'target2']) assert.ok(crowded.nodes.some(item => item.id === id));
assert.equal(crowded.edges.flatMap(item => item.members).length, 52, 'Grouping must retain every underlying record');
assert.equal(project(large, { focus: false }).nodes.length, 36);
assert.equal(project(large, { focus: false }).edges.length, 34);
const protectedNodes = [...Array.from({ length: 12 }, (_, i) => node(`exchange${i}`, { node_type: 'GLOBAL_EXCHANGE_DEPOSIT' })), ...Array.from({ length: 12 }, (_, i) => node(`bridge${i}`, { node_type: 'BRIDGE_PROTOCOL' }))];
assert.equal(project({ nodes: protectedNodes, edges: [] }).nodes.length, 24, 'Targets and bridges are exempt from the eight-wallet cap even without visible flow');
const structuredRouters = Array.from({ length: 12 }, (_, i) => node(`dex${i}`, { metadata: { public_entity: { kind: 'DEX_ROUTER', label: 'Uniswap V3' } } }));
const dexView = project({ nodes: [seed, ...structuredRouters], edges: structuredRouters.map(item => edge(`to-${item.id}`, 'seed', item.id, 1)) });
assert.equal(dexView.nodes.length, 13, 'Structured public DEX nodes must never be capped or clustered');
for (const item of structuredRouters) assert.equal(dexView.roles.get(item.id), 'bridge');

// Hops are on-chain distances from seeds, independent of fiat/source offsets, and inbound scans work too.
const chainNodes = [node('origin', { node_zone: 'FIAT_BANK', hop: 0 }), node('pakd', { node_type: 'PAKD_USER_ACCOUNT', hop: 4 }), node('seed', { hop: 5, metadata: { is_initial_wallet: true } }), ...Array.from({ length: 6 }, (_, i) => node(`hop${i + 1}`, { hop: i + 6 }))];
const chainEdges = [edge('o', 'origin', 'pakd'), edge('s', 'pakd', 'seed'), ...Array.from({ length: 6 }, (_, i) => edge(`h${i}`, i ? `hop${i}` : 'seed', `hop${i + 1}`))];
const chained = project({ nodes: chainNodes, edges: chainEdges });
assert.equal(chained.hops.get('seed'), 0); assert.equal(chained.hops.get('hop1'), 1); assert.equal(chained.hops.get('hop6'), 6);
assert.equal(chained.columns.get('origin'), 0); assert.equal(chained.columns.get('pakd'), 0); assert.equal(chained.columns.get('seed'), 1);
assert.equal(chained.columns.get('hop1'), 2); assert.equal(chained.columns.get('hop6'), 3);
const incoming = project({ nodes: [seed, wallet], edges: [edge('inbound', 'wallet', 'seed')] });
assert.equal(incoming.hops.get('wallet'), 1);
const sourceFallback = project({ nodes: [node('source', { node_zone: 'FIAT_BANK' }), wallet], edges: [edge('entry', 'source', 'wallet')] });
assert.equal(sourceFallback.hops.get('wallet'), 1);
const btc = project({ nodes: [node('seed', { chain: 'BTC', asset: 'BTC', metadata: { is_seed_wallet: true, wallet_hop: 0 } }), node('tx', { chain: 'BTC', asset: 'BTC', node_type: 'BITCOIN_TRANSACTION', hop: 1, metadata: { wallet_hop: 0 } }), node('out', { chain: 'BTC', asset: 'BTC', hop: 2, metadata: { wallet_hop: 1 } })], edges: [edge('b1', 'seed', 'tx', 1, { asset: 'BTC', chain: 'BTC' }), edge('b2', 'tx', 'out', 1, { asset: 'BTC', chain: 'BTC' })] });
assert.equal(btc.hops.get('tx'), 0); assert.equal(btc.hops.get('out'), 1);
assert.equal(btc.columns.get('tx'), 2, 'Transaction nodes are not seed wallets merely because they share Hop 0');
const declared = project({ nodes: [seed, node('deep', { hop: 20, metadata: { onchain_hop: 6 } })], edges: [edge('depth', 'seed', 'deep')] });
assert.equal(declared.hops.get('deep'), 6);
const preferredDepth = project({ nodes: [seed, node('deep', { metadata: { wallet_hop: 4, onchain_hop: 6 } })], edges: [edge('depth', 'seed', 'deep')] });
assert.equal(preferredDepth.hops.get('deep'), 4);

// Widths compare like units only: a huge spam quantity must not dwarf the actual ETH trail.
const values = project({ nodes: [seed, node('a'), node('b'), node('c')], edges: [edge('a', 'seed', 'a', 1), edge('b', 'seed', 'b', .01), edge('c', 'seed', 'c', 9000000000, { asset: 'PEPE' })] }, { focus: false });
assert.ok(values.edges.find(item => item.target === 'a').width > values.edges.find(item => item.target === 'b').width);
assert.ok(values.edges.find(item => item.target === 'a').critical);
const ranked = project({ nodes: [seed, node('aaa-small'), node('zzz-main')], edges: [edge('small', 'seed', 'aaa-small', 1), edge('main', 'seed', 'zzz-main', 100)] });
assert.ok(layout(ranked).positions.get('zzz-main').y < layout(ranked).positions.get('aaa-small').y, 'The main comparable-unit trail must appear before small flows');
for (const item of values.edges) assert.ok(Number.isFinite(item.width) && item.width >= 2 && item.width <= 8);

// Source chronology must remain visible before larger downstream sums; chain rows stay consistent across lanes.
const origins = project({ nodes: [node('victim', { node_zone: 'FIAT_BANK', node_type: 'VICTIM', hop: 0 }), node('mule', { node_zone: 'FIAT_BANK', node_type: 'MULE', hop: 1 }), node('va', { node_type: 'PAKD_VA', hop: 2 }), node('pakd-account', { node_type: 'PAKD_USER_ACCOUNT', hop: 4 })], edges: [edge('v-m', 'victim', 'mule', 1), edge('m-va', 'mule', 'va', 20), edge('va-p', 'va', 'pakd-account', 1000)] });
const originLayout = layout(origins);
assert.ok(originLayout.positions.get('victim').y < originLayout.positions.get('mule').y, 'The initial victim must appear above the mule regardless of downstream totals');
assert.ok(originLayout.positions.get('mule').y < originLayout.positions.get('va').y);
assert.ok(originLayout.positions.get('va').y < originLayout.positions.get('pakd-account').y);
const branchNodes = ['ETHEREUM', 'SOLANA', 'BTC'].flatMap(chain => [node(`${chain}-seed`, { chain, metadata: { is_seed_wallet: true } }), node(`${chain}-target`, { chain: null, node_type: 'PAKD_RECEIVER_ACCOUNT', hop: 2 })]);
const branchEdges = ['ETHEREUM', 'SOLANA', 'BTC'].map(chain => edge(`${chain}-flow`, `${chain}-seed`, `${chain}-target`, 1, { chain }));
const branchView = project({ nodes: branchNodes, edges: branchEdges }), branchLayout = layout(branchView);
for (const suffix of ['seed', 'target']) {
  assert.ok(branchLayout.positions.get(`BTC-${suffix}`).y < branchLayout.positions.get(`SOLANA-${suffix}`).y, 'Bitcoin must precede Solana consistently across columns');
  assert.ok(branchLayout.positions.get(`SOLANA-${suffix}`).y < branchLayout.positions.get(`ETHEREUM-${suffix}`).y, 'Solana must precede Ethereum consistently across columns');
}
assert.deepEqual(plain([...branchLayout.positions]), plain([...layout(project({ nodes: [...branchNodes].reverse(), edges: [...branchEdges].reverse() })).positions]));

// Four lanes and non-overlapping card centers remain deterministic for disconnected cycles and reordered input.
const cycles = { nodes: [seed, node('a', { hop: 1 }), node('b', { hop: 2 }), node('isolated', { hop: 6 })], edges: [edge('sa', 'seed', 'a'), edge('ab', 'a', 'b'), edge('ba', 'b', 'a'), edge('aa', 'a', 'a')] };
const cycleView = project(cycles, { focus: false });
const cycleLayout = layout(cycleView);
assert.deepEqual(plain([...cycleLayout.positions]), plain([...layout(project({ nodes: [...cycles.nodes].reverse(), edges: [...cycles.edges].reverse() }, { focus: false })).positions]));
for (const view of [crowded, chained, cycleView, project({ nodes: [], edges: [] })]) {
  const result = layout(view);
  assert.equal(result.columns.length, 4);
  assert.ok(result.width >= 1050 && result.width <= 1200); assert.ok(result.height >= 480);
  assert.equal(result.cardWidth, 210); assert.equal(result.cardHeight, 84);
  assert.equal(result.positions.size, view.nodes.length);
  for (const item of view.nodes) {
    const point = result.positions.get(item.id);
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
    assert.equal(point.x, result.columns[view.columns.get(item.id)].x);
    assert.ok(point.x >= 105 && point.x <= result.width - 105);
    assert.ok(point.y >= 42 && point.y <= result.height - 42);
  }
  for (let col = 0; col < 4; col++) {
    const ys = view.nodes.filter(item => view.columns.get(item.id) === col).map(item => result.positions.get(item.id).y).sort((a, b) => a - b);
    for (let i = 1; i < ys.length; i++) assert.ok(ys[i] - ys[i - 1] >= 194, '84px cards need at least 110px clear vertical gap');
  }
}
console.log('PASS: forensic roles, immutable bundling, token identities, candidate evidence, focus, clusters, on-chain hops and deterministic four-column cards.');
