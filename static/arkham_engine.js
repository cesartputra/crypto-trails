// Physics touches presentation copies only; evidence always stays on `original`.
const ArkhamEngine = (() => {
  const endpoint = value => value && typeof value === 'object' ? value.id : value;
  const compare = (a, b) => String(a.id ?? `${endpoint(a.source)}:${endpoint(a.target)}`).localeCompare(String(b.id ?? `${endpoint(b.source)}:${endpoint(b.target)}`));
  const finitePoint = point => point && Number.isFinite(point.x) && Number.isFinite(point.y);

  function prepare(view, { focalId, width = 1120, height = 720, pins = new Map(), previous = new Map() } = {}) {
    width = Number.isFinite(width) && width > 0 ? width : 1120;
    height = Number.isFinite(height) && height > 0 ? height : 720;
    const originals = [...(view.nodes || [])].sort(compare), ids = new Set(originals.map(node => node.id));
    if (!ids.has(focalId)) focalId = originals.find(node => node.metadata?.is_seed_wallet || node.metadata?.is_initial_wallet || /SEED|INITIAL/.test(node.node_type || ''))?.id ?? originals[0]?.id;
    const edges = [...(view.edges || view.links || [])].filter(edge => ids.has(endpoint(edge.source)) && ids.has(endpoint(edge.target))).sort(compare);
    const incoming = new Map(originals.map(node => [node.id, []])), outgoing = new Map(originals.map(node => [node.id, []]));
    for (const edge of edges) {
      outgoing.get(endpoint(edge.source)).push(endpoint(edge.target));
      incoming.get(endpoint(edge.target)).push(endpoint(edge.source));
    }
    function distances(adjacency) {
      const result = new Map(), queue = [];
      if (ids.has(focalId)) { result.set(focalId, 0); queue.push(focalId); }
      for (let index = 0; index < queue.length; index++) for (const id of adjacency.get(queue[index])) {
        if (!result.has(id)) { result.set(id, result.get(queue[index]) + 1); queue.push(id); }
      }
      return result;
    }
    const upstream = distances(incoming), downstream = distances(outgoing);
    const sideOf = id => id === focalId ? 'hub' : (upstream.get(id) ?? Infinity) < (downstream.get(id) ?? Infinity) ? 'in' : (downstream.get(id) ?? Infinity) < (upstream.get(id) ?? Infinity) ? 'out' : 'other';
    const stage = record => {
      let zero = null;
      for (const value of [record?.chronological_step, record?.metadata?.chronological_step]) {
        if (value == null || typeof value === 'boolean' || String(value).trim() === '' || !Number.isInteger(Number(value))) continue;
        if (Number(value) > 0) return Number(value);
        if (Number(value) === 0) zero = 0;
      }
      return zero;
    };
    const stages = new Map(originals.map(node => [node.id, stage(node)]));
    const edgeStages = new Set(edges.flatMap(edge => (edge.members || [edge]).map(stage)));
    const chronological = originals.every(node => stages.get(node.id) != null && stages.get(node.id) >= 0 && stages.get(node.id) <= 5)
      && [0, 1, 2, 3, 4, 5].every(value => [...stages.values()].includes(value))
      && [1, 2, 3, 4, 5, 6].every(value => edgeStages.has(value))
      && edges.some(edge => (edge.members || [edge]).some(record => stage(record) === 6 && stages.get(endpoint(record.source)) === 5 && stages.get(endpoint(record.target)) === 3));
    const chronologicalGroups = Array.from({ length: 6 }, () => []);
    if (chronological) for (const node of originals) chronologicalGroups[stages.get(node.id)].push(node.id);
    const groups = { in: [], out: [], other: [], hub: [] };
    for (const node of originals) groups[sideOf(node.id)].push(node.id);
    const nodes = originals.map(original => {
      const side = sideOf(original.id), group = chronological ? chronologicalGroups[stages.get(original.id)] : groups[side], index = group.indexOf(original.id);
      const rows = Math.max(1, Math.floor((height - 120) / 100)), row = index % rows, column = Math.floor(index / rows);
      const rowCount = Math.min(rows, group.length - column * rows), spread = rowCount > 1 ? row / (rowCount - 1) * 2 - 1 : 0;
      const targetX = chronological ? width * .48 + (stages.get(original.id) - stages.get(focalId)) * 180 : side === 'hub' ? width * .48 : side === 'in' ? width * .25 - column * 100 : side === 'out' ? width * (.75 + Math.abs(spread) * .08) + column * 100 : width * .48 + column * 100;
      const targetY = chronological ? height * .5 + (index - (group.length - 1) / 2) * 110 : side === 'hub' ? height * .5 : side === 'other' ? height * .5 + 150 + row * 100 : height * .5 + (row - (rowCount - 1) / 2) * 100;
      const saved = previous.get(original.id), pin = pins.get(original.id);
      const position = finitePoint(pin) ? pin : side === 'hub' ? { x: targetX, y: targetY } : finitePoint(saved) ? saved : { x: targetX, y: targetY };
      const node = { ...original, original, side, radius: side === 'hub' ? 38 : 30, targetX, targetY, x: position.x, y: position.y, vx: 0, vy: 0 };
      delete node.fx; delete node.fy;
      if (finitePoint(pin) || side === 'hub') { node.fx = position.x; node.fy = position.y; }
      return node;
    });
    const links = edges.map(original => {
      const source = endpoint(original.source), target = endpoint(original.target), a = sideOf(source), b = sideOf(target);
      const side = target === focalId ? 'in' : source === focalId ? 'out' : a === b && (a === 'in' || a === 'out') ? a : upstream.has(source) && upstream.has(target) && upstream.get(source) > upstream.get(target) ? 'in' : downstream.has(source) && downstream.has(target) && downstream.get(source) < downstream.get(target) ? 'out' : 'other';
      return { ...original, original, source, target, side };
    });
    return { nodes, links, focalId, width, height };
  }

  function create(model, { d3, onTick = () => {}, requestFrame, cancelFrame = () => {}, reducedMotion = false, paused = false } = {}) {
    const nodes = model.nodes, byId = new Map(nodes.map(node => [node.id, node]));
    const links = model.links.map(link => ({ ...link, source: byId.get(endpoint(link.source)), target: byId.get(endpoint(link.target)) })).filter(link => link.source && link.target);
    let simulation, frame = null, generation = 0, frozen = Boolean(paused), stopped = false, remaining = 240, alpha = 1;
    if (d3?.forceSimulation && d3.forceManyBody && d3.forceCollide && d3.forceLink && d3.forceX && d3.forceY) {
      // A single injected RAF owns both D3 and fallback animation, including disposal.
      simulation = d3.forceSimulation(nodes).stop()
        .force('charge', d3.forceManyBody().strength(-420))
        .force('collision', d3.forceCollide().radius(44).iterations(3))
        .force('link', d3.forceLink(links).id(node => node.id).distance(180).strength(.35))
        .force('x', d3.forceX(node => node.targetX).strength(.09))
        .force('y', d3.forceY(node => node.targetY).strength(.07));
    }
    const fixed = node => Number.isFinite(node.fx) && Number.isFinite(node.fy);
    function collide() {
      for (let pass = 0; pass < 4; pass++) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy);
        if (distance >= 88 || fixed(a) && fixed(b)) continue;
        if (distance < .001) { dx = .001; dy = (i + j) % 2 ? .001 : -.001; distance = Math.hypot(dx, dy); }
        const move = (88 - distance) / (fixed(a) || fixed(b) ? 1 : 2), x = dx / distance * move, y = dy / distance * move;
        if (!fixed(a)) { a.x -= x; a.y -= y; }
        if (!fixed(b)) { b.x += x; b.y += y; }
      }
    }
    function step() {
      if (simulation) simulation.tick();
      else {
        // shortcut: focused investigation graphs use pairwise repulsion; use a quadtree for thousands of visible nodes.
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j], dx = b.x - a.x || .01, dy = b.y - a.y || .01;
          const distance = Math.max(1, Math.hypot(dx, dy)), force = Math.min(8, 420 / distance) * alpha;
          a.vx -= dx / distance * force; a.vy -= dy / distance * force;
          b.vx += dx / distance * force; b.vy += dy / distance * force;
        }
        for (const { source, target } of links) {
          if (source === target) continue;
          const dx = target.x - source.x, dy = target.y - source.y, distance = Math.max(1, Math.hypot(dx, dy));
          const force = (distance - 180) * .035 * alpha, x = dx / distance * force, y = dy / distance * force;
          source.vx += x; source.vy += y; target.vx -= x; target.vy -= y;
        }
        for (const node of nodes) {
          node.vx = Math.max(-24, Math.min(24, (node.vx + (node.targetX - node.x) * .09 * alpha) * .72));
          node.vy = Math.max(-24, Math.min(24, (node.vy + (node.targetY - node.y) * .07 * alpha) * .72));
          node.x += node.vx; node.y += node.vy;
        }
        alpha *= .976;
      }
      for (const node of nodes) if (fixed(node)) { node.x = node.fx; node.y = node.fy; node.vx = 0; node.vy = 0; }
      collide();
    }
    function tick(iterations = 1) {
      if (frozen || stopped) return api;
      const count = Math.max(0, Math.min(1000, Number.isFinite(iterations) ? Math.floor(iterations) : 1));
      for (let i = 0; i < count; i++) step();
      if (count) onTick();
      return api;
    }
    function cancel() {
      generation++;
      if (frame !== null) cancelFrame(frame);
      frame = null;
      simulation?.stop();
    }
    function schedule() {
      if (!requestFrame || reducedMotion || frozen || stopped || !remaining || frame !== null) return;
      const token = generation;
      frame = requestFrame(() => {
        if (token !== generation || stopped || frozen) return;
        frame = null;
        tick(); remaining--;
        schedule();
      });
    }
    function restart() {
      cancel(); stopped = false; remaining = 240; alpha = 1;
      simulation?.alpha(1);
      if (reducedMotion && !frozen) tick(160);
      else schedule();
      return api;
    }
    function stop() { stopped = true; cancel(); return api; }
    function setFrozen(value) {
      frozen = Boolean(value);
      if (frozen) cancel(); else restart();
      return api;
    }
    function pin(id, x, y) {
      const node = byId.get(id);
      if (!node || !Number.isFinite(x) || !Number.isFinite(y)) return api;
      node.x = node.fx = x; node.y = node.fy = y; node.vx = 0; node.vy = 0;
      onTick(); if (!reducedMotion) restart();
      return api;
    }
    function unpin(id) {
      const node = byId.get(id);
      if (!node) return api;
      if (id === model.focalId) { node.x = node.fx = model.width * .48; node.y = node.fy = model.height * .5; }
      else { delete node.fx; delete node.fy; }
      onTick(); restart();
      return api;
    }
    const api = { nodes, links, tick, restart, stop, setFrozen, pin, unpin };
    if (reducedMotion && !frozen) tick(160);
    else schedule();
    return api;
  }

  function fanPaths(source, target, count = 1, width = 2) {
    if (!finitePoint(source) || !finitePoint(target)) return [];
    const strands = Math.max(1, Math.min(24, Math.ceil(Number(count) || 1)));
    const thickness = Number.isFinite(width) ? Math.max(1, Math.min(8, width)) : 2;
    const dx = target.x - source.x, dy = target.y - source.y, distance = Math.hypot(dx, dy);
    const sr = (Number.isFinite(source.radius) ? source.radius : 30) + 3, tr = (Number.isFinite(target.radius) ? target.radius : 30) + 5;
    const number = value => Math.round(value * 100) / 100;
    const point = (x, y) => `${number(x)} ${number(y)}`;
    return Array.from({ length: strands }, (_, index) => {
      const offset = (index - (strands - 1) / 2) * (3 + thickness);
      if (distance < 1) {
        const spread = 55 + index * 3;
        return `M${point(source.x - sr * .6, source.y - sr * .8)} C${point(source.x - spread, source.y - spread * 2)} ${point(source.x + spread, source.y - spread * 2)} ${point(source.x + sr * .6, source.y - sr * .8)}`;
      }
      const bend = Math.min(48, distance * .10) + offset;
      const cx = (source.x + target.x) / 2 - dy / distance * bend, cy = (source.y + target.y) / 2 + dx / distance * bend;
      const startDistance = Math.max(1, Math.hypot(cx - source.x, cy - source.y)), endDistance = Math.max(1, Math.hypot(cx - target.x, cy - target.y));
      const sx = source.x + (cx - source.x) / startDistance * sr, sy = source.y + (cy - source.y) / startDistance * sr;
      const tx = target.x + (cx - target.x) / endDistance * tr, ty = target.y + (cy - target.y) / endDistance * tr;
      return `M${point(sx, sy)} Q${point(cx, cy)} ${point(tx, ty)}`;
    });
  }

  return { prepare, create, fanPaths };
})();
