// Animation follows original evidence chronology, independently of the displayed focal node.
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = { Map, Set, Math, Number, String, JSON, Date };
vm.createContext(context);
vm.runInContext(readFileSync(resolve(__dirname, '../static/forensic_graph.js'), 'utf8') + '\nthis.graphModule = ForensicGraph;', context);
vm.runInContext(readFileSync(resolve(__dirname, '../static/arkham_engine.js'), 'utf8') + '\nthis.engine = ArkhamEngine;', context);
const { animationSteps, visualize, project, label } = context.graphModule;
assert.equal(typeof animationSteps, 'function', 'The shared animation sequence must be available without the UI or a layout engine');
const plain = value => JSON.parse(JSON.stringify(value));
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
const node = (id, chronological_step, node_type, node_zone = 'ONCHAIN_WALLET') => ({ id, label: id, chronological_step, node_type, node_zone });
const edge = (id, source, target, step, minute = step) => ({ id, source, target, chronological_step: step, asset: 'USDT', chain: 'ETHEREUM', crypto_amount: 1, timestamp: `2026-10-21T00:${String(minute).padStart(2, '0')}:00Z` });
const nodes = [node('victim', 0, 'VICTIM', 'FIAT_BANK'), node('primary', 1, 'PRIMARY_SCAMMER', 'FIAT_BANK'),
  ...['a', 'b', 'c', 'd'].map(id => node(`mule-${id}`, 2, 'MULE', 'FIAT_BANK')),
  node('pakd', 3, 'PAKD_USER_ACCOUNT', 'CRYPTO_EXCHANGE_BRIDGE'),
  ...['a', 'b', 'c', 'd'].flatMap(id => [node(`hop1-${id}`, 4, 'INITIAL_WALLET'), node(`hop2-${id}`, 5, 'UNHOSTED_WALLET')])];
const originals = [edge('victim-primary', 'victim', 'primary', 1),
  ...['a', 'b', 'c', 'd'].map((id, i) => edge(`fan-${id}`, 'primary', `mule-${id}`, 2, 2 + i)),
  ...['a', 'b', 'c', 'd'].map(id => edge(`intake-${id}`, `mule-${id}`, 'pakd', 3, 10)),
  ...['a', 'b', 'c', 'd'].map(id => edge(`withdraw-${id}`, 'pakd', `hop1-${id}`, 4, 20)),
  ...['a', 'b', 'c', 'd'].flatMap((id, i, all) => [edge(`layer-${id}`, `hop1-${id}`, `hop2-${id}`, 5, 30), edge(`cross-${id}`, `hop1-${id}`, `hop2-${all[(i + 1) % 4]}`, 5, 31)]),
  ...['a', 'b', 'c', 'd'].map(id => edge(`loop-${id}`, `hop2-${id}`, 'pakd', 6, 40))];
// Explicit aliases must remain numeric and null must never become stage zero.
delete originals[2].chronological_step; originals[2].metadata = { chronological_step: '2' };
delete originals[3].chronological_step; originals[3].hop_order = 2;
delete originals[4].chronological_step; originals[4].chronological_step = null; originals[4].metadata = { animation_order: 2 };
const graph = freeze({ nodes: [...nodes].reverse(), edges: [...originals].reverse(), focalId: 'pakd' });
const before = JSON.stringify(graph), steps = animationSteps(graph);
assert.deepEqual(plain(steps.map(step => step.order)), [1, 2, 3, 4, 5, 6]);
assert.deepEqual(plain(steps.map(step => step.edges.length)), [1, 4, 4, 4, 8, 4]);
assert.equal(steps[0].edges[0], originals[0]);
assert.deepEqual(plain(steps[0].sourceIds), ['victim']);
assert.deepEqual(plain(steps[0].targetIds), ['primary']);
assert.deepEqual(plain(steps[1].edges.map(item => item.id)), ['fan-a', 'fan-b', 'fan-c', 'fan-d']);
assert.deepEqual(plain(steps[5].targetIds), ['pakd']);
assert.match(steps[5].label, /yang sama/, 'A proven return to the account that funded the withdrawal is identified as the same-account loopback');
assert.ok(steps.every(step => typeof step.label === 'string' && step.label.length));
assert.equal(new Set(steps.flatMap(step => step.edges)).size, originals.length);
assert.ok(steps.flatMap(step => step.edges).every(item => originals.includes(item)), 'Steps retain the original evidence records');
assert.equal(JSON.stringify(graph), before);
for (const sortTime of [false, true]) {
  const view = visualize(graph, { focalId: 'pakd', focus: false, sortTime });
  const projected = animationSteps(view);
  assert.equal(projected[0].edges[0], originals[0]);
  assert.deepEqual(plain(projected.map(step => step.order)), [1, 2, 3, 4, 5, 6]);
  const model = context.engine.prepare(view, { focalId: 'pakd' });
  const link = model.links.find(item => item.members.includes(originals[0]));
  assert.equal(link.chronological_step, 1);
  assert.equal(link.members[0], originals[0]);
  assert.equal(link.original, view.edges.find(item => item.id === link.id));
  assert.deepEqual(plain(animationSteps({ nodes: model.nodes, edges: model.links }).map(step => step.order)), [1, 2, 3, 4, 5, 6]);
}
const completeLayout = context.engine.prepare(visualize(graph, { focalId: 'pakd', focus: false }), { focalId: 'pakd', width: 1120, height: 720 });
const columns = [0, 1, 2, 3, 4, 5].map(stage => completeLayout.nodes.filter(item => item.chronological_step === stage));
assert.ok(columns.every((items, stage) => !stage || items[0].targetX > columns[stage - 1][0].targetX), 'A complete cyclic chronology stays left-to-right, including wallets that return to the focal account');
assert.equal(completeLayout.nodes.find(item => item.id === 'pakd').fx, 1120 * .48);
assert.ok(columns.every(items => new Set(items.map(item => item.targetX)).size === 1));
assert.equal(new Set(columns[4].map(item => item.targetY)).size, 4, 'Withdrawal fan-out keeps four distinct wallet rows');
assert.equal(new Set(columns[5].map(item => item.targetY)).size, 4);
const metadataLayout = context.engine.prepare({ nodes: graph.nodes.map(item => ({ ...item, chronological_step: 0, metadata: { chronological_step: item.chronological_step } })), edges: graph.edges }, { focalId: 'pakd', width: 1120, height: 720 });
assert.deepEqual(plain(metadataLayout.nodes.map(item => [item.id, item.targetX, item.targetY])), plain(completeLayout.nodes.map(item => [item.id, item.targetX, item.targetY])), 'Explicit metadata chronology must survive default zero node fields in the physics model');
const pinnedLayout = context.engine.prepare(visualize(graph, { focalId: 'pakd', focus: false }), { focalId: 'pakd', pins: new Map([['hop2-a', { x: 200, y: 100 }]]) });
assert.equal(pinnedLayout.nodes.find(item => item.id === 'hop2-a').fx, 200);
const partialLayout = context.engine.prepare({ nodes: graph.nodes, edges: graph.edges.filter(item => item.chronological_step !== 6) }, { focalId: 'pakd' });
const legacyPartialLayout = context.engine.prepare({ nodes: graph.nodes.map(({ chronological_step, ...item }) => item), edges: graph.edges.filter(item => item.chronological_step !== 6) }, { focalId: 'pakd' });
assert.deepEqual(plain(partialLayout.nodes.map(item => [item.id, item.targetX, item.targetY])), plain(legacyPartialLayout.nodes.map(item => [item.id, item.targetX, item.targetY])), 'A partial graph retains the existing focal layout');

// A displayed line can aggregate different phases; its members, never its first member, determine animation.
const samePair = freeze({ nodes: nodes.slice(0, 2), edges: [originals[0], edge('later-same-pair', 'victim', 'primary', 6, 41)] });
const bundle = project(samePair, { focus: false }).edges[0];
assert.equal(bundle.chronological_step, undefined, 'A mixed-phase bundle must not claim the first phase for all its transactions');
assert.equal(bundle.metadata.chronological_step, undefined);
assert.deepEqual(plain(animationSteps({ ...samePair, edges: [bundle] }).map(step => step.order)), [1, 6]);
assert.equal(bundle.members[0], samePair.edges[1]);

// Legacy records infer the six phases from victim-directed topology; returning to a previous account stays last.
const legacy = freeze({ nodes: nodes.map(({ chronological_step, ...item }) => item), edges: originals.map(({ chronological_step, hop_order, metadata, ...item }) => item).reverse() });
const legacySteps = animationSteps(legacy);
assert.deepEqual(plain(legacySteps.map(step => step.order)), [1, 2, 3, 4, 5, 6]);
assert.equal(legacySteps[0].edges[0].id, 'victim-primary');
assert.deepEqual(plain(legacySteps[5].edges.map(item => item.id)), ['loop-a', 'loop-b', 'loop-c', 'loop-d']);
const defaults = freeze({ nodes: legacy.nodes.map(item => ({ ...item, chronological_step: 0 })), edges: legacy.edges.map(item => ({ ...item, chronological_step: 0 })) });
assert.deepEqual(plain(animationSteps(defaults).map(step => [step.order, step.edges.length])), [[1, 1], [2, 4], [3, 4], [4, 4], [5, 8], [6, 4]], 'Default stage-zero fields on old/live records are placeholders, not evidence that every node is a victim');
const liveDefaults = freeze({ nodes: ['seed', 'wallet', 'next'].map(id => ({ id, chain: 'ETHEREUM', chronological_step: 0 })), edges: [{ id: 'live2', source: 'wallet', target: 'next', chronological_step: 0 }, { id: 'live1', source: 'seed', target: 'wallet', chronological_step: 0 }] });
assert.deepEqual(plain(animationSteps(liveDefaults).map(step => [step.order, step.edges[0].id])), [[1, 'live1'], [2, 'live2']]);
const expanded = freeze({ nodes: [nodes[0], nodes[1], node('seed', 4, 'INITIAL_WALLET'), ...['expanded-a', 'expanded-b', 'expanded-c'].map(id => node(id, 0, 'UNHOSTED_WALLET'))],
  edges: [edge('expanded-next', 'expanded-a', 'expanded-b', 0, 30), edge('expanded-last', 'expanded-b', 'expanded-c', 0, 31), edge('expanded-funding', 'seed', 'expanded-a', 0, 40), originals[0]] });
const expandedBefore = JSON.stringify(expanded);
assert.deepEqual(plain(animationSteps(expanded).map(step => [step.order, step.edges.map(item => item.id)])), [[1, ['victim-primary']], [5, ['expanded-funding']], [6, ['expanded-next']], [7, ['expanded-last']]], 'Default-zero on-chain expansion nodes inherit the funding chronology and never become victims or animate ahead of their funding');
assert.doesNotMatch(animationSteps(expanded).find(step => step.order === 6).label, /loopback|yang sama/i, 'An ordinary sixth-stage transfer is not proof of a same-account loopback');
assert.equal(JSON.stringify(expanded), expandedBefore);
const expandedReturn = freeze({ nodes: [...expanded.nodes, node('pakd-return', 3, 'PAKD_USER_ACCOUNT', 'CRYPTO_EXCHANGE_BRIDGE')], edges: [...expanded.edges, edge('withdraw-to-seed', 'pakd-return', 'seed', 4, 20), edge('expanded-return', 'expanded-c', 'pakd-return', 0, 50)] });
assert.deepEqual(plain(animationSteps(expandedReturn).map(step => [step.order, step.edges.map(item => item.id)])), [[1, ['victim-primary']], [4, ['withdraw-to-seed']], [5, ['expanded-funding']], [6, ['expanded-next']], [7, ['expanded-last']], [8, ['expanded-return']]], 'An expanded return follows all of its on-chain funding hops rather than jumping back to stage six');
assert.match(animationSteps(expandedReturn).at(-1).label, /yang sama/, 'The original withdrawal-account proof identifies a loopback even beyond six stages');
const metadataStage = freeze({ nodes: nodes.slice(0, 2), edges: [{ ...edge('metadata-stage', 'victim', 'primary', 0), metadata: { chronological_step: 6 } }] });
assert.equal(animationSteps(metadataStage)[0].order, 6, 'A schema default zero must not mask an explicit positive metadata stage');
const attributedAccount = freeze({ ...legacy, nodes: legacy.nodes.map(item => item.id === 'pakd' ? { ...item, chain: 'ETHEREUM', address: '0xPAKD-ACCOUNT-DEPOSIT' } : item) });
assert.deepEqual(plain(animationSteps(attributedAccount).map(step => [step.order, step.edges.length])), [[1, 1], [2, 4], [3, 4], [4, 4], [5, 8], [6, 4]], 'An account can carry chain/address attribution without becoming an on-chain wallet');

const settlement = freeze({ nodes: [...legacy.nodes, node('return-deposit', undefined, 'DIRECT_EXCHANGE_DEPOSIT'), node('receiver', undefined, 'PAKD_RECEIVER_ACCOUNT', 'CRYPTO_EXCHANGE_BRIDGE'), node('cashout', undefined, 'P2P_OFFRAMP_SETTLEMENT', 'CRYPTO_EXCHANGE_BRIDGE'), node('final-bank', undefined, 'FINAL_CASHOUT', 'FIAT_BANK')],
  edges: [...legacy.edges.filter(item => !item.id.startsWith('loop-')), { ...edge('return-deposit', 'hop2-a', 'return-deposit', undefined, 40) }, edge('return-credit', 'return-deposit', 'receiver', undefined, 41), edge('sell', 'receiver', 'cashout', undefined, 42), edge('settle', 'cashout', 'final-bank', undefined, 43)] });
assert.deepEqual(plain(animationSteps(settlement).at(-1).edges.map(item => item.id)), ['return-deposit', 'return-credit', 'sell', 'settle'], 'Internal records after on-chain return must stay downstream of the withdrawal and layering');
assert.doesNotMatch(animationSteps(settlement).at(-1).label, /yang sama/, 'A different receiving exchange must not be described as the original PAKD account');

// Missing or malformed dates use deterministic topology and IDs; timestamp zero remains valid evidence.
const undated = freeze({ nodes: legacy.nodes, edges: legacy.edges.map((item, index) => ({ ...item, timestamp: index % 2 ? null : 'invalid' })) });
assert.equal(animationSteps(undated)[0].edges[0].id, 'victim-primary');
assert.deepEqual(plain(animationSteps(undated).map(step => step.edges.map(item => item.id))), plain(animationSteps({ ...undated, nodes: [...undated.nodes].reverse(), edges: [...undated.edges].reverse() }).map(step => step.edges.map(item => item.id))));
const dated = freeze({ nodes: nodes.slice(0, 2), edges: [edge('z', 'victim', 'primary', 1, 1), { ...edge('a', 'victim', 'primary', 1), timestamp: 0 }, { ...edge('unknown', 'victim', 'primary', 1), timestamp: 'invalid' }] });
assert.deepEqual(plain(animationSteps(dated)[0].edges.map(item => item.id)), ['a', 'z', 'unknown']);
assert.deepEqual(plain(animationSteps({ nodes: [], edges: [] })), []);
const bankMules = freeze([1, 2, 3, 4].map(index => ({ id: `account-${index}`, node_zone: 'FIAT_BANK', node_type: 'MULE', label: `Bank Mule ${index} (Bank Example - 1234567890${index})` })));
assert.deepEqual(bankMules.map(label), ['bank mule 1', 'bank mule 2', 'bank mule 3', 'bank mule 4'], 'Compact bank mule labels retain their distinguishing ordinal');
assert.equal(bankMules[0].label, 'Bank Mule 1 (Bank Example - 12345678901)');
console.log('PASS: Shared victim-first chronology, six complete phases, stable timestamps, same-account loopback and immutable bundled evidence.');
