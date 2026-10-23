// Deterministic bounded network layout: node tests/frontend_network_selfcheck.cjs
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function networkLayout(');
assert.ok(start >= 0, 'Network must arrange nodes without an ongoing simulation');
const context = { Map, Set, Math, Number, String };
vm.createContext(context);
vm.runInContext(source.slice(start, source.indexOf('function layoutNodes(', start)), context);
const nodes = Array.from({ length: 12 }, (_, index) => ({ id: `n${index}` }));
const edges = [
  { source: 'n0', target: 'n1' }, { source: 'n1', target: 'n2' }, { source: 'n2', target: 'n0' },
  { source: 'n1', target: 'n1' }, { source: 'n0', target: 'n1' }, { source: 'n3', target: 'n4' }
];
const unchanged = JSON.stringify({ nodes, edges });
const layout = context.networkLayout(nodes, edges);
const reversed = context.networkLayout([...nodes].reverse(), [...edges].reverse());
assert.deepEqual(Array.from(layout.positions), Array.from(reversed.positions), 'Input order must not move existing evidence');
assert.equal(JSON.stringify({ nodes, edges }), unchanged);
function check(result, expected) {
  assert.equal(result.positions.size, expected);
  assert.ok(Number.isFinite(result.width) && Number.isFinite(result.height));
  const points = Array.from(result.positions.values());
  for (const point of points) {
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
    assert.ok(point.x >= 40 && point.x <= result.width - 40);
    assert.ok(point.y >= 40 && point.y <= result.height - 40);
  }
  for (let a = 0; a < points.length; a++) for (let b = a + 1; b < points.length; b++)
    assert.ok(Math.hypot(points[a].x - points[b].x, points[a].y - points[b].y) >= 100, 'Node circles and labels need clearance');
}
check(layout, nodes.length);
check(context.networkLayout([], []), 0);
check(context.networkLayout([{ id: 'seed' }], []), 1);
const hubNodes = Array.from({ length: 33 }, (_, index) => ({ id: `hub${index}` }));
check(context.networkLayout(hubNodes, hubNodes.slice(1).map(node => ({ source: 'hub0', target: node.id }))), 33);
const largeNodes = Array.from({ length: 256 }, (_, index) => ({ id: `manual${index}` }));
const largeEdges = Array.from({ length: 512 }, (_, index) => ({ source: `manual${index % 256}`, target: `manual${(index * 31 + 1) % 256}` }));
check(context.networkLayout(largeNodes, largeEdges), 256);
const viewport = { clientWidth: 1200, clientHeight: 400, style: {}, setAttribute() {}, replaceChildren() {} };
context.$ = () => viewport;
context.state = {};
context.visibleGraph = () => ({ nodes, edges });
vm.runInContext(source.slice(source.indexOf('function layoutNodes('), source.indexOf('function edgeGeometry(')), context);
context.layoutNodes();
assert.ok(context.state.baseWidth > layout.width, 'Landscape viewport should spread the network across the available width');
assert.ok(context.state.baseWidth <= layout.width * 1.7 + .001, 'Horizontal expansion stays bounded');
assert.equal(context.state.baseHeight, layout.height, 'Circles and vertical geometry retain their scale');
viewport.clientWidth = 360; viewport.clientHeight = 640;
context.layoutNodes();
assert.equal(context.state.baseWidth, layout.width, 'Portrait viewport keeps the original network proportions');
let resize, scheduled, redraws = 0;
context.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} };
context.clearTimeout = () => {};
context.setTimeout = callback => { scheduled = callback; return 1; };
context.stopAnimation = () => {};
context.resetView = () => {};
context.renderGraph = () => { redraws++; context.state.viewportWidth = viewport.clientWidth; context.state.viewportHeight = viewport.clientHeight; };
context.state.graph = { nodes, edges };
context.state.viewportWidth = 970; context.state.viewportHeight = 537;
viewport.clientWidth = 970; viewport.clientHeight = 480;
vm.runInContext(source.slice(source.indexOf('let graphResizeTimer;'), source.indexOf('async function init(')), context);
resize(); scheduled();
assert.equal(redraws, 1, 'Height-only canvas changes must refresh graph coordinates and aspect');
resize(); scheduled();
assert.equal(redraws, 1, 'An unchanged canvas must not trigger another layout');
viewport.clientWidth = 880;
resize(); scheduled();
assert.equal(redraws, 2, 'Width changes still refresh layout');
vm.runInContext(source.slice(source.indexOf('function edgeGeometry('), source.indexOf('function renderGraph(')), context);
assert.equal(typeof context.parallelEdgeSlots, 'function', 'Reciprocal edges must share a geometry group');
const reciprocal = [{ id: 'ab', source: 'a', target: 'b' }, { id: 'ba', source: 'b', target: 'a' }];
const reciprocalBefore = JSON.stringify(reciprocal), slots = context.parallelEdgeSlots(reciprocal);
const ab = context.edgeGeometry({ x: 0, y: 0 }, { x: 190, y: 0 }, slots.get('ab').index, slots.get('ab').count);
const ba = context.edgeGeometry({ x: 190, y: 0 }, { x: 0, y: 0 }, slots.get('ba').index, slots.get('ba').count);
assert.ok(Math.abs(ab.labelY - ba.labelY) > 20, 'Opposite directed flows need separate curves and hit regions');
assert.equal(JSON.stringify(reciprocal), reciprocalBefore);
const mixed = context.parallelEdgeSlots([...reciprocal, { id: 'ab2', source: 'a', target: 'b' }, { id: 'ba2', source: 'b', target: 'a' }]);
const paths = ['ab', 'ab2', 'ba', 'ba2'].map(id => {
  const slot = mixed.get(id), reverse = id.startsWith('ba');
  return context.edgeGeometry({ x: reverse ? 190 : 0, y: 0 }, { x: reverse ? 0 : 190, y: 0 }, slot.index, slot.count).labelY;
});
assert.equal(new Set(paths).size, 4, 'Parallel and reciprocal transfers retain distinct geometry slots');
assert.ok(context.edgeGeometry({ x: 0, y: 0 }, { x: 190, y: 0 }).path.startsWith('M36 0 '), 'Directed edges must start outside circular nodes');
for (const pair of [[{ x: 0, y: 0 }, { x: 190, y: 0 }], [{ x: 0, y: 0 }, { x: 0, y: 190 }], [{ x: 0, y: 0 }, { x: 0, y: 0 }]]) {
  const geometry = context.edgeGeometry(...pair, 1, 3);
  assert.ok(!/NaN|Infinity/.test(geometry.path));
  assert.ok(Number.isFinite(geometry.labelX) && Number.isFinite(geometry.labelY));
}
for (const geometry of [context.edgeGeometry({ x: 0, y: 0 }, { x: 0, y: 0 }, 511, 512), context.edgeGeometry({ x: 0, y: 0 }, { x: 190, y: 0 }, 511, 512)]) {
  assert.ok(Array.from(geometry.path.matchAll(/-?\d+(?:\.\d+)?/g), match => Number(match[0])).every(value => Math.abs(value) <= 250), 'Parallel/self-loop curves stay inside padded fit bounds');
}
console.log('PASS: deterministic network, disconnected evidence, hubs, cycles, self-loops, clearance and 256-node bounds.');
