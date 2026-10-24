// Offline physics, evidence isolation, directed polarity and animation lifecycle.
const { readFileSync, existsSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const modulePath = resolve(__dirname, '../static/arkham_engine.js');
assert.ok(existsSync(modulePath), 'Arkham physics engine must exist');
const context = { Map, Set, Math, Number, String, JSON };
vm.createContext(context);
vm.runInContext(readFileSync(modulePath, 'utf8') + '\nthis.engine = ArkhamEngine;', context);
const { prepare, create, fanPaths } = context.engine;
const plain = value => JSON.parse(JSON.stringify(value));
const graph = {
  nodes: ['bank', 'funding', 'seed', 'wallet', 'exchange', 'isolated'].map(id => ({ id, metadata: { is_seed_wallet: id === 'seed' } })),
  edges: [
    { id: 'f1', source: 'bank', target: 'funding' },
    { id: 'f2', source: 'funding', target: 'seed' },
    { id: 'o1', source: 'seed', target: 'wallet' },
    { id: 'o2', source: 'wallet', target: 'exchange' },
    { id: 'loop', source: 'exchange', target: 'exchange' }
  ]
};
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
freeze(graph);
const before = JSON.stringify(graph);
const model = prepare(graph, { width: 1000, height: 720 });
const byId = new Map(model.nodes.map(node => [node.id, node]));
assert.equal(model.focalId, 'seed');
assert.equal(byId.get('bank').side, 'in', 'Entire directed upstream funding path stays left');
assert.equal(byId.get('funding').side, 'in');
assert.equal(byId.get('wallet').side, 'out');
assert.equal(byId.get('exchange').side, 'out');
assert.equal(byId.get('isolated').side, 'other', 'Disconnected evidence must not be invented as outflow');
assert.equal(byId.get('seed').radius, 38);
assert.equal(byId.get('seed').fx, 480);
assert.equal(byId.get('seed').fy, 360);
assert.ok(byId.get('bank').x < byId.get('seed').x);
assert.ok(byId.get('exchange').x > byId.get('seed').x);
assert.equal(byId.get('bank').original, graph.nodes[0]);
assert.deepEqual(plain(model), plain(prepare({ nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() }, { width: 1000, height: 720 })), 'Initial positions and link ordering are deterministic');

const saved = prepare(graph, { width: 1000, height: 720, pins: new Map([['wallet', { x: 240, y: 130 }]]), previous: new Map([['exchange', { x: 915, y: 175 }], ['bank', { x: NaN, y: Infinity }]]) });
assert.equal(saved.nodes.find(n => n.id === 'wallet').fx, 240);
assert.equal(saved.nodes.find(n => n.id === 'exchange').x, 915);
assert.ok(saved.nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)));
const cycle = prepare({ nodes: ['seed', 'a', 'b', 'isolated'].map(id => ({ id })), edges: [{ source: 'seed', target: 'a' }, { source: 'a', target: 'b' }, { source: 'b', target: 'seed' }, { source: 'a', target: 'a' }] }, { focalId: 'seed', width: 1000, height: 720 });
assert.equal(cycle.nodes.find(n => n.id === 'a').side, 'out');
assert.equal(cycle.nodes.find(n => n.id === 'b').side, 'in');
assert.equal(cycle.nodes.find(n => n.id === 'isolated').side, 'other');
const disconnected = prepare({ nodes: ['seed', ...Array.from({ length: 18 }, (_, i) => `neutral${i}`)].map(id => ({ id })), edges: [] }, { focalId: 'seed', width: 1000, height: 720 });
for (let i = 0; i < disconnected.nodes.length; i++) for (let j = i + 1; j < disconnected.nodes.length; j++) {
  const a = disconnected.nodes[i], b = disconnected.nodes[j];
  assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= 88, `Initial neutral nodes remain separate: ${a.id}, ${b.id}`);
}

let ticks = 0;
const simulation = create(model, { onTick: () => ticks++ });
simulation.tick(240);
assert.equal(ticks, 1, 'A batch of warmup steps renders once');
assert.ok(simulation.nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y) && Number.isFinite(n.vx) && Number.isFinite(n.vy)));
assert.equal(byId.get('seed').x, 480);
assert.equal(byId.get('seed').y, 360);
for (let i = 0; i < simulation.nodes.length; i++) for (let j = i + 1; j < simulation.nodes.length; j++) {
  const a = simulation.nodes[i], b = simulation.nodes[j];
  assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= 87, `Collision clearance: ${a.id}, ${b.id}`);
}
simulation.pin('wallet', 123, 234);
simulation.tick(20);
assert.equal(byId.get('wallet').x, 123);
assert.equal(byId.get('wallet').fx, 123);
simulation.unpin('wallet');
assert.equal(byId.get('wallet').fx, undefined);
simulation.tick(40);
assert.notEqual(byId.get('wallet').x, 123, 'Unlocked nodes rejoin physics');
simulation.pin('seed', 200, 250);
simulation.unpin('seed');
assert.equal(byId.get('seed').fx, 480, 'Unlocking the focal node restores the default anchor');
simulation.setFrozen(true);
const frozenPositions = simulation.nodes.map(n => [n.x, n.y]);
simulation.tick(30);
assert.deepEqual(simulation.nodes.map(n => [n.x, n.y]), frozenPositions, 'Freeze must stop actual physics');
simulation.pin('wallet', 320, 270);
assert.equal(byId.get('wallet').x, 320, 'Dragging remains available while physics is frozen');
simulation.setFrozen(false);
simulation.tick(10);
simulation.stop();
assert.equal(JSON.stringify(graph), before, 'Forces and pins never mutate original evidence or links');

const investigation = {
  nodes: ['seed', 'bank', 'mule', 'pakd', 'funding', 'extra-a', 'extra-b', ...['a', 'b', 'c'].flatMap(branch => [1, 2, 3].map(hop => `${branch}${hop}`))].map(id => ({ id })),
  edges: [
    { source: 'bank', target: 'mule' }, { source: 'mule', target: 'pakd' }, { source: 'pakd', target: 'funding' }, { source: 'funding', target: 'seed' },
    { source: 'seed', target: 'extra-a' }, { source: 'seed', target: 'extra-b' },
    ...['a', 'b', 'c'].flatMap(branch => [1, 2, 3].map(hop => ({ source: hop === 1 ? 'seed' : `${branch}${hop - 1}`, target: `${branch}${hop}` })))
  ]
};
const compact = create(prepare(investigation, { focalId: 'seed', width: 1000, height: 720, pins: new Map([['a2', { x: 800, y: 450 }]]) }), {});
compact.tick(240);
const compactWidth = Math.max(...compact.nodes.map(node => node.x + 44)) - Math.min(...compact.nodes.map(node => node.x - 44));
const compactHeight = Math.max(...compact.nodes.map(node => node.y + 44)) - Math.min(...compact.nodes.map(node => node.y - 44));
assert.ok(compactWidth <= 1300 && compactHeight <= 936, `Ordinary investigations stay readable when fitted: ${compactWidth} × ${compactHeight}`);
for (let i = 0; i < compact.nodes.length; i++) for (let j = i + 1; j < compact.nodes.length; j++) {
  const a = compact.nodes[i], b = compact.nodes[j];
  assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= 87.99, `Compact layout preserves circle clearance: ${a.id}, ${b.id}`);
}
assert.equal(compact.nodes.find(node => node.id === 'a2').x, 800);
assert.equal(compact.nodes.find(node => node.id === 'a2').y, 450);
assert.equal(compact.nodes.find(node => node.id === 'seed').x, 480);
compact.stop();

const callbacks = new Map(); let nextFrame = 1, renders = 0;
const animated = create(prepare(graph, { width: 1000, height: 720 }), {
  onTick: () => renders++,
  requestFrame: callback => { const id = nextFrame++; callbacks.set(id, callback); return id; },
  cancelFrame: id => callbacks.delete(id)
});
assert.equal(callbacks.size, 1);
const first = [...callbacks.entries()][0]; callbacks.delete(first[0]); first[1]();
assert.equal(renders, 1);
assert.equal(callbacks.size, 1);
const stale = [...callbacks.values()][0];
animated.stop();
assert.equal(callbacks.size, 0, 'Stopping cancels the old graph animation frame');
stale();
assert.equal(renders, 1, 'An already queued callback cannot update a disposed graph');
const paused = create(prepare(graph, { width: 1000, height: 720 }), { paused: true, requestFrame: callback => { callbacks.set(1, callback); return 1; }, cancelFrame: id => callbacks.delete(id) });
const pausedBefore = plain(paused.nodes);
paused.tick(30);
assert.deepEqual(plain(paused.nodes), pausedBefore);
assert.equal(callbacks.size, 0, 'Persistent pause creates no new animation');
const reduced = create(prepare(graph, { width: 1000, height: 720 }), { reducedMotion: true, requestFrame: callback => { callbacks.set(1, callback); return 1; } });
assert.equal(callbacks.size, 0, 'Reduced motion uses only a finite layout warmup');
const reducedOthers = reduced.nodes.filter(node => node.id !== 'wallet').map(node => [node.id, node.x, node.y]);
reduced.pin('wallet', 330, 240);
assert.deepEqual(reduced.nodes.filter(node => node.id !== 'wallet').map(node => [node.id, node.x, node.y]), reducedOthers, 'Reduced-motion dragging must not synchronously warm the entire graph on each pointer move');
assert.equal(reduced.nodes.find(node => node.id === 'wallet').x, 330);
reduced.stop(); paused.stop();

const source = { x: 10, y: 20, radius: 38 }, target = { x: 300, y: 20, radius: 30 };
assert.equal(fanPaths(source, target).length, 1);
const fan = fanPaths(source, target, 12, 2);
assert.equal(fan.length, 12);
assert.equal(new Set(fan).size, 12, 'Multi-transfer fans need distinct curvature');
assert.equal(fanPaths(source, target, 1000).length, 24, 'Huge transfer counts have bounded SVG work');
assert.ok(fan.every(path => /^M[\d. -]+ Q[\d. -]+$/.test(path) && !/NaN|Infinity/.test(path)));
assert.ok(!fan[0].startsWith('M10 20'), 'Fan endpoints stay outside the node circles');
assert.ok(fanPaths(source, source, 3).every(path => !/NaN|Infinity/.test(path) && path.includes('C')), 'Self-transfers draw visible finite loops');
assert.ok(fanPaths({ x: NaN, y: 0 }, target).length === 0, 'Invalid geometry cannot enter an SVG path');
assert.deepEqual(plain(prepare({ nodes: [], edges: [] }, {})).nodes, []);
console.log('PASS: Arkham directed layout, offline physics, pins, freeze, animation lifecycle and fan geometry.');
