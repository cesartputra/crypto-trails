// Evidence stays on the original records; this module creates only a presentation projection.
const ForensicGraph = (() => {
  const major = ['ETH', 'BTC', 'SOL', 'USDT', 'USDC', 'DAI', 'WBTC', 'BNB'];
  const compareId = (a, b) => String(a.id) < String(b.id) ? -1 : String(a.id) > String(b.id) ? 1 : 0;
  const positive = value => value != null && typeof value !== 'boolean' && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 0;
  const hopNumber = value => value != null && typeof value !== 'boolean' && String(value).trim() !== '' && Number.isInteger(Number(value)) && Number(value) >= 0 ? Number(value) : null;
  const symbol = record => String(record.asset || '').toUpperCase();
  const chain = record => String(record.chain || record.metadata?.chain || record.chain_branch || record.metadata?.chain_branch || '').toUpperCase();
  const contract = record => {
    const value = record.contract || record.contract_address || record.metadata?.token_contract || record.metadata?.contract_address || '';
    return /^0x/i.test(String(value)) ? String(value).toLowerCase() : String(value);
  };
  const identity = record => JSON.stringify([symbol(record), chain(record), contract(record)]);
  const candidate = (record, graph) => record.candidate === true || record.metadata?.is_candidate === true || /CANDIDATE|UNVERIFIED/.test(String(record.metadata?.correlation_status || '').toUpperCase()) || /CANDIDATE/.test(record.edge_type || '') || (record.edge_type === 'CROSS_CHAIN_BRIDGE' && (record.metadata?.is_live === true || record.metadata?.simulation === false || graph?.simulation === false || /LIVE|MAINNET/.test(String(record.metadata?.evidence_source || '').toUpperCase())) && record.metadata?.bridge_link_verified !== true);
  const isSeed = node => node.metadata?.is_seed_wallet === true || node.metadata?.is_initial_wallet === true || /SEED|INITIAL/.test(String(node.node_type || '').toUpperCase());

  const attributedType = node => [node.node_type, node.entity_type, node.entity_kind, node.metadata?.entity_type, node.metadata?.entity_kind, node.metadata?.public_entity?.kind].filter(Boolean).join(' ').toUpperCase();
  function isExchange(node) {
    return /EXCHANGE|CUSTODIAL|CUSTODY|PAKD_MIRROR_MATCH|PAKD_RECEIVER|P2P_ESCROW_DEPOSIT/.test(attributedType(node)) || Boolean(node.metadata?.mirror_match?.pakd_name);
  }

  function role(node) {
    if (node.cluster_members) return 'cluster';
    const type = String(node.node_type || '').toUpperCase(), metadata = node.metadata || {};
    if (/^(PAKD_USER_ACCOUNT|PAKD_VA|PAKD_FIAT_DEPOSIT|PAKD_CRYPTO_CONVERSION|VICTIM|FNA_HANDOFF_ACCOUNT)$/.test(type)) return 'source';
    if (isSeed(node)) return 'seed';
    const attributed = attributedType(node);
    if (node.is_bridge_node || metadata.bridge_reference || /BRIDGE|DEX|ROUTER/.test(attributed)) return 'bridge';
    if (isExchange(node) || /P2P_ESCROW|P2P_OFFRAMP|FINAL_CASHOUT/.test(attributed) || metadata.recovery_institution === 'ISSUER') return 'target';
    return node.node_zone === 'FIAT_BANK' ? 'source' : 'wallet';
  }

  function preferredAssets(graph) {
    const filter = String(graph.live_metadata?.token_filter || '').toUpperCase();
    return [...new Set([...(filter && filter !== 'ALL' ? [filter] : []), ...(graph.nodes || []).filter(isSeed).map(symbol).filter(Boolean)])];
  }

  function totalsFor(records) {
    const totals = new Map();
    for (const record of records) {
      const amount = positive(record.crypto_amount);
      if (!amount || !symbol(record)) continue;
      const key = identity(record), current = totals.get(key);
      if (current) {
        // shortcut: JS numbers are display estimates; use backend decimal records for exact accounting.
        const sum = current.amount + amount;
        if (Number.isFinite(sum)) current.amount = sum;
      } else totals.set(key, { asset: symbol(record), chain: chain(record), ...(contract(record) ? { contract: contract(record) } : {}), amount });
    }
    return [...totals.values()];
  }

  function primaryOrder(preferred) {
    const rank = asset => preferred.includes(asset) ? preferred.indexOf(asset) : major.includes(asset) ? preferred.length + major.indexOf(asset) : preferred.length + major.length;
    return (a, b) => rank(a.asset) - rank(b.asset) || identity(a).localeCompare(identity(b));
  }

  function amountSummary(recordOrRecords) {
    const records = Array.isArray(recordOrRecords) ? recordOrRecords : recordOrRecords?.members || [recordOrRecords].filter(Boolean);
    const first = !Array.isArray(recordOrRecords) ? recordOrRecords : null;
    const totals = first?.totals || totalsFor(records).sort(primaryOrder([]));
    const selected = totals.find(item => item.asset === first?.asset && item.chain === (first?.chain || '') && (!first?.contract || item.contract === first.contract)) || totals[0];
    const count = records.length, suffix = ` (${count} tx)`;
    if (selected) return `${new Intl.NumberFormat('en-US', { maximumSignificantDigits: 7 }).format(selected.amount)} ${selected.asset}${suffix}`;
    const fiat = records.reduce((sum, item) => sum + positive(item.amount_idr), 0);
    if (fiat > 0 && Number.isFinite(fiat)) return `Rp ${new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 }).format(fiat)}${suffix}`;
    return `${first?.asset ? `0 ${first.asset} · ` : ''}${count} tx`;
  }

  function project(graph, { focus = true, cluster = true } = {}) {
    const originals = [...(graph.nodes || [])].sort(compareId), byId = new Map(originals.map(node => [node.id, node]));
    const edges = [...(graph.edges || [])].filter(edge => byId.has(edge.source) && byId.has(edge.target)).sort(compareId);
    const requestTargets = new Set((graph.governance_requests || []).map(request => request.target_node_id));
    const roles = new Map(originals.map(node => [node.id, role(node) === 'wallet' && requestTargets.has(node.id) ? 'target' : role(node)])), hops = new Map(), columns = new Map();
    const preferred = preferredAssets(graph), orderPrimary = primaryOrder(preferred);
    const primaryAllowed = record => major.includes(symbol(record)) || preferred.includes(symbol(record)) || record.metadata?.token_verified === true || record.metadata?.is_verified_token === true;
    const largest = new Map();
    for (const edge of edges) largest.set(identity(edge), Math.max(largest.get(identity(edge)) || 0, positive(edge.crypto_amount)));
    function material(record) {
      if (positive(record.amount_idr) > 0 || positive(record.amount_usd ?? record.usd_amount ?? record.metadata?.amount_usd) > 0) return true;
      const amount = positive(record.crypto_amount), endpointRoles = [roles.get(record.source), roles.get(record.target)];
      const protectedEndpoint = endpointRoles.some(value => value === 'target' || value === 'bridge');
      // Focus hides ordinary-wallet transfers below 0.1% of the largest identical asset/chain/contract amount, never endpoint evidence.
      return amount > 0 && primaryAllowed(record) && (protectedEndpoint || !endpointRoles.includes('wallet') || amount >= (largest.get(identity(record)) || 0) * .001);
    }
    const hiddenEdges = focus ? edges.filter(edge => !material(edge)) : [];
    const activeEdges = focus ? edges.filter(material) : edges;

    // Walk on-chain evidence separately so fiat/PAKD steps never inflate wallet hops.
    const adjacency = new Map(originals.map(node => [node.id, new Set()]));
    for (const edge of edges) {
      if (roles.get(edge.source) === 'source' || roles.get(edge.target) === 'source') continue;
      adjacency.get(edge.source).add(edge.target); adjacency.get(edge.target).add(edge.source);
    }
    const seeds = originals.filter(node => isSeed(node) && roles.get(node.id) !== 'source');
    const queue = seeds.map(node => node.id);
    for (const id of queue) hops.set(id, 0);
    if (!queue.length) {
      for (const source of originals.filter(node => roles.get(node.id) === 'source')) {
        hops.set(source.id, 0);
        for (const edge of edges) if (edge.source === source.id && roles.get(edge.target) !== 'source' && !hops.has(edge.target)) { hops.set(edge.target, 1); queue.push(edge.target); }
      }
    }
    for (let index = 0; index < queue.length; index++) {
      const id = queue[index];
      for (const next of [...adjacency.get(id)].sort()) if (!hops.has(next)) { hops.set(next, hops.get(id) + 1); queue.push(next); }
    }
    for (const node of originals) {
      const explicit = hopNumber(node.metadata?.wallet_hop) ?? hopNumber(node.metadata?.onchain_hop);
      const hop = roles.get(node.id) === 'seed' ? 0 : explicit ?? hops.get(node.id) ?? hopNumber(node.hop) ?? 1;
      hops.set(node.id, hop);
      columns.set(node.id, roles.get(node.id) === 'source' ? 0 : roles.get(node.id) === 'seed' ? 1 : roles.get(node.id) === 'target' ? 3 : roles.get(node.id) === 'bridge' ? 2 : hop >= 2 ? 3 : 2);
    }
    const activeIds = new Set(activeEdges.flatMap(edge => [edge.source, edge.target]));
    const visible = originals.filter(node => !focus || roles.get(node.id) !== 'wallet' || activeIds.has(node.id));

    const scoreById = new Map(originals.map(node => [node.id, []]));
    for (const edge of activeEdges) {
      scoreById.get(edge.source).push(edge);
      if (edge.target !== edge.source) scoreById.get(edge.target).push(edge);
    }
    function score(node) {
      const records = scoreById.get(node.id) || [];
      const fiat = records.reduce((sum, item) => sum + positive(item.amount_idr), 0);
      if (fiat > 0 && Number.isFinite(fiat)) return { kind: 0, key: 'IDR', amount: fiat };
      const totals = totalsFor(records).sort(orderPrimary), total = totals[0];
      return total ? { kind: preferred.includes(total.asset) ? 1 : major.includes(total.asset) ? 2 : 3, key: identity(total), amount: total.amount } : { kind: 4, key: '', amount: 0 };
    }
    const scores = new Map(originals.map(node => [node.id, score(node)]));
    function significance(a, b) {
      const left = scores.get(a.id) || { kind: 4, key: '', amount: 0 }, right = scores.get(b.id) || { kind: 4, key: '', amount: 0 };
      // Different units have no shared financial ranking; asset identity breaks ties before quantity.
      return left.kind - right.kind || left.key.localeCompare(right.key) || right.amount - left.amount || compareId(a, b);
    }
    const clustered = new Map(), clusters = [];
    if (focus && cluster) for (let column = 0; column < 4; column++) {
      const ordinary = visible.filter(node => columns.get(node.id) === column && roles.get(node.id) === 'wallet').sort(significance);
      if (ordinary.length <= 8) continue;
      const members = ordinary.slice(8).sort(compareId);
      let id = `__forensic_cluster:${column}`;
      while (byId.has(id)) id = `_${id}`;
      const cluster = { id, label: `+${members.length} Dompet Nilai Kecil Lainnya`, node_type: 'FORENSIC_CLUSTER', cluster_members: members, metadata: { synthetic_cluster: true } };
      for (const member of members) clustered.set(member.id, id);
      clusters.push(cluster); roles.set(id, 'cluster'); columns.set(id, column); hops.set(id, Math.min(...members.map(member => hops.get(member.id))));
    }
    const nodes = [...visible.filter(node => !clustered.has(node.id)), ...clusters].sort((a, b) => columns.get(a.id) - columns.get(b.id) || (roles.get(b.id) === 'target') - (roles.get(a.id) === 'target') || (roles.get(a.id) === 'cluster') - (roles.get(b.id) === 'cluster') || significance(a, b));
    const shownIds = new Set(nodes.map(node => node.id)), bundles = new Map();
    for (const edge of edges) {
      const source = clustered.get(edge.source) || edge.source, target = clustered.get(edge.target) || edge.target;
      if (!shownIds.has(source) || !shownIds.has(target)) continue;
      const key = JSON.stringify([source, target]);
      if (!bundles.has(key)) bundles.set(key, { source, target, members: [], active: false });
      const bundle = bundles.get(key); bundle.members.push(edge);
      if (!focus || material(edge)) bundle.active = true;
    }
    const resultEdges = [...bundles.values()].filter(bundle => bundle.active).map(bundle => {
      const totals = totalsFor(bundle.members).sort(orderPrimary), primary = totals[0], uncertain = bundle.members.some(record => candidate(record, graph));
      const first = bundle.members[0];
      const fiat = bundle.members.reduce((sum, item) => sum + positive(item.amount_idr), 0);
      const usd = bundle.members.reduce((sum, item) => sum + positive(item.amount_usd ?? item.usd_amount ?? item.metadata?.amount_usd), 0);
      return { ...first, id: `flow:${encodeURIComponent(bundle.source)}:${encodeURIComponent(bundle.target)}`, source: bundle.source, target: bundle.target,
        members: bundle.members, totals, asset: primary?.asset || first.asset || '', crypto_amount: primary?.amount || 0, chain: primary?.chain || first.chain || '',
        ...(primary?.contract ? { contract: primary.contract } : {}), amount_idr: Number.isFinite(fiat) ? fiat : 0, amount_usd: Number.isFinite(usd) ? usd : 0,
        transaction_count: bundle.members.length, candidate: uncertain, metadata: { ...first.metadata, is_candidate: uncertain }, critical: false, width: 2 };
    }).sort(compareId);
    const scales = new Map();
    const valueFor = bundle => bundle.amount_idr > 0 ? { key: 'IDR', amount: bundle.amount_idr } : bundle.amount_usd > 0 ? { key: 'USD', amount: bundle.amount_usd } : { key: identity(bundle), amount: bundle.crypto_amount };
    for (const bundle of resultEdges) {
      const value = valueFor(bundle), scale = scales.get(value.key) || { max: 0, total: 0 };
      scale.max = Math.max(scale.max, value.amount); scale.total += value.amount; scales.set(value.key, scale);
    }
    for (const bundle of resultEdges) {
      const value = valueFor(bundle), scale = scales.get(value.key);
      const primary = value.key === 'IDR' || value.key === 'USD' || preferred.includes(bundle.asset) || (!preferred.length && major.includes(bundle.asset));
      bundle.width = scale.max > 0 ? 2 + 6 * Math.sqrt(value.amount / scale.max) : 2;
      bundle.critical = !bundle.candidate && (roles.get(bundle.target) === 'target' || (primary && scale.total > 0 && value.amount / scale.total > .15));
    }
    const hiddenNodes = originals.filter(node => !shownIds.has(node.id));
    return { nodes, edges: resultEdges, roles, hops, columns, scores, hiddenCount: hiddenNodes.length, clusterCount: clusters.length, hiddenNodes, hiddenEdges };
  }

  function usdValue(record) {
    for (const value of [record.amount_usd, record.usd_amount, record.metadata?.amount_usd, record.value_usd, record.metadata?.value_usd, record.usd_value, record.metadata?.usd_value]) {
      if (value != null && typeof value !== 'boolean' && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0) return Number(value);
    }
    return null;
  }

  function evidenceTime(record) {
    const value = record.timestamp ?? record.occurred_at ?? record.metadata?.timestamp;
    if (value == null || typeof value === 'boolean' || String(value).trim() === '') return null;
    const time = typeof value === 'number' ? new Date(value).getTime() : Date.parse(value);
    return Number.isFinite(time) ? time : null;
  }

  const timeOrder = (a, b) => (evidenceTime(a) ?? Infinity) - (evidenceTime(b) ?? Infinity) || compareId(a, b);
  const trustedName = node => node.metadata?.public_entity?.label || node.metadata?.public_entity?.name || (/EXCHANGE|CUSTODIAL|CUSTODY|BRIDGE|DEX|ROUTER|PAKD|PUJK/.test(attributedType(node)) && !/UNATTRIBUTED|UNKNOWN/.test(attributedType(node)) ? node.entity_name || node.entity_label || node.metadata?.entity_name || node.metadata?.entity_label || node.label : '');

  function label(node) {
    const known = node.cluster_members ? node.label : trustedName(node) || (role(node) === 'source' ? node.label : '');
    const addressLabel = /^(?:0x[0-9a-fA-F]{16,}|[1-9A-HJ-NP-Za-km-z]{26,90}|bc1[a-zA-Z0-9]{20,})$/.test(node.label || '') ? node.label : '';
    const text = String(known || node.address || node.wallet_address || node.metadata?.address || addressLabel || node.id || node.label || '').replace(/[<>]/g, '').replace(/\s+/g, ' ').trim();
    return known ? (text.length > 13 ? `${text.slice(0, 10)}...` : text).toLowerCase() : text.length > 14 ? `${text.slice(0, 7)}...${text.slice(-4)}` : text;
  }

  function timeline(edges, count = 48) {
    const times = [], records = (edges || []).flatMap(edge => edge.members || [edge]);
    for (const record of records) { const time = evidenceTime(record); if (time != null) times.push(time); }
    const undatedCount = records.length - times.length;
    if (!times.length) return { start: null, end: null, bins: [], undatedCount };
    let start = Math.min(...times), end = Math.max(...times);
    if (start === end) { start -= 43200000; end += 43200000; }
    const size = Number.isInteger(count) && count > 0 ? Math.min(count, 512) : 48, width = (end - start) / size;
    const bins = Array.from({ length: size }, (_, index) => ({ start: start + index * width, end: start + (index + 1) * width, count: 0 }));
    for (const time of times) bins[Math.min(size - 1, Math.floor((time - start) / width))].count++;
    return { start, end, bins, undatedCount };
  }

  function visualize(graph, { focalId, focus = true, flow = 'ALL', group = 'CHAIN', range = null, sortTime = true } = {}) {
    const originals = [...(graph.nodes || [])].sort(compareId), byId = new Map(originals.map(node => [node.id, node]));
    let sourceEdges = (graph.edges || []).filter(edge => byId.has(edge.source) && byId.has(edge.target));
    if (!byId.has(focalId)) {
      const seeds = (graph.nodes || []).filter(node => isSeed(node) && role(node) !== 'source'), splits = originals.filter(node => node.node_type === 'PAKD_USER_ACCOUNT');
      const degree = id => sourceEdges.filter(edge => edge.source === id).length;
      focalId = (seeds[0] || splits.sort((a, b) => degree(b.id) - degree(a.id) || compareId(a, b))[0] || graph.nodes?.[0])?.id ?? null;
    }
    const hasRange = Array.isArray(range) && range.length === 2 && range.every(value => typeof value === 'number' && Number.isFinite(value));
    sourceEdges = sourceEdges.filter(edge => (!focus || usdValue(edge) == null || usdValue(edge) >= .10) && (!hasRange || (evidenceTime(edge) != null && evidenceTime(edge) >= range[0] && evidenceTime(edge) <= range[1])));
    const focused = project({ ...graph, edges: sourceEdges }, { focus, cluster: false });
    const allowed = new Set(focused.edges.flatMap(bundle => bundle.members));
    // A priced transfer or an attributed endpoint remains material without a supported native token price.
    if (focus) for (const edge of sourceEdges) if (usdValue(edge) >= .10 || (positive(edge.crypto_amount) && [focused.roles.get(edge.source), focused.roles.get(edge.target)].some(value => value === 'target' || value === 'bridge'))) allowed.add(edge);
    let records = focus ? sourceEdges.filter(edge => allowed.has(edge)) : sourceEdges;
    const visibleIds = new Set(focused.nodes.map(node => node.id));
    for (const edge of records) { visibleIds.add(edge.source); visibleIds.add(edge.target); }
    const direction = String(flow).toUpperCase(), directed = direction === 'IN' || direction === 'OUT';
    const reached = new Set(focalId == null ? [] : [focalId]);
    if (directed) {
      const queue = [...reached];
      for (let index = 0; index < queue.length; index++) for (const edge of records) {
        const from = direction === 'IN' ? edge.target : edge.source, to = direction === 'IN' ? edge.source : edge.target;
        if (from === queue[index] && !reached.has(to)) { reached.add(to); queue.push(to); }
      }
      records = records.filter(edge => reached.has(edge.source) && reached.has(edge.target));
    }
    const activeIds = new Set(records.flatMap(edge => [edge.source, edge.target]));
    let nodes = originals.filter(node => visibleIds.has(node.id) && (!hasRange || activeIds.has(node.id)) && (!directed || reached.has(node.id)));
    if (focalId != null && !nodes.some(node => node.id === focalId)) nodes.push(byId.get(focalId));
    const shownOriginals = new Set(nodes.map(node => node.id)), remap = new Map(), ownerGroups = new Map();
    if (String(group).toUpperCase() === 'OWNER') {
      for (const node of nodes) {
        if (node.id === focalId) continue;
        const explicit = node.owner_id ?? node.entity_id ?? node.metadata?.owner_id ?? node.metadata?.entity_id ?? node.metadata?.public_entity?.owner_id ?? node.metadata?.public_entity?.entity_id ?? node.metadata?.public_entity?.id;
        const name = trustedName(node), owner = explicit != null && typeof explicit !== 'object' && typeof explicit !== 'boolean' && String(explicit).trim() ? ['id', String(explicit)] : name ? ['entity', String(name).toLowerCase()] : null;
        if (!owner) continue;
        const key = JSON.stringify([...owner, chain(node)]);
        if (!ownerGroups.has(key)) ownerGroups.set(key, []);
        ownerGroups.get(key).push(node);
      }
      for (const [key, members] of ownerGroups) {
        if (members.length < 2) continue;
        let id = `owner:${encodeURIComponent(key)}`;
        while (byId.has(id)) id = `owner:${id}`;
        const owner = JSON.parse(key), first = members[0];
        const grouped = { id, label: trustedName(first) || owner[1], chain: chain(first), node_type: 'OWNER_GROUP', cluster_members: members, metadata: { synthetic_owner: true, owner_id: owner[1] } };
        for (const member of members) remap.set(member.id, id);
        nodes.push(grouped);
      }
      nodes = nodes.filter(node => !remap.has(node.id));
    }
    const originalRecords = new Map(), rewritten = records.map(record => {
      if (!remap.has(record.source) && !remap.has(record.target)) return record;
      const proxy = { ...record, source: remap.get(record.source) || record.source, target: remap.get(record.target) || record.target };
      originalRecords.set(proxy, record); return proxy;
    });
    const result = project({ ...graph, nodes, edges: rewritten }, { focus: false, cluster: false });
    let unknownUsdCount = 0;
    for (const bundle of result.edges) {
      bundle.members = bundle.members.map(record => originalRecords.get(record) || record);
      if (sortTime) bundle.members.sort(timeOrder);
      const values = bundle.members.map(usdValue), known = values.filter(value => value != null);
      unknownUsdCount += values.length - known.length;
      if (known.length) bundle.amount_usd = known.reduce((sum, value) => sum + value, 0);
      else delete bundle.amount_usd;
    }
    const roles = new Map(result.nodes.map(node => [node.id, focused.roles.get(node.id) || result.roles.get(node.id)]));
    for (const node of result.nodes.filter(node => node.cluster_members)) {
      const memberRoles = node.cluster_members.map(member => focused.roles.get(member.id));
      roles.set(node.id, ['target', 'bridge', 'source', 'seed', 'wallet'].find(value => memberRoles.includes(value)) || 'wallet');
    }
    const hops = new Map(), adjacency = new Map(result.nodes.map(node => [node.id, new Set()]));
    for (const edge of result.edges) { adjacency.get(edge.source).add(edge.target); adjacency.get(edge.target).add(edge.source); }
    const queue = focalId == null ? [] : [focalId];
    if (focalId != null) hops.set(focalId, 0);
    for (let index = 0; index < queue.length; index++) for (const next of [...adjacency.get(queue[index])].sort()) if (!hops.has(next)) { hops.set(next, hops.get(queue[index]) + 1); queue.push(next); }
    for (const node of result.nodes) if (!hops.has(node.id)) hops.set(node.id, 1);
    if (sortTime) {
      const firstTime = bundle => bundle.members.reduce((first, record) => Math.min(first, evidenceTime(record) ?? Infinity), Infinity);
      result.edges.sort((a, b) => firstTime(a) - firstTime(b) || compareId(a, b));
      const nodeTimes = new Map();
      for (const edge of result.edges) for (const id of [edge.source, edge.target]) nodeTimes.set(id, Math.min(nodeTimes.get(id) ?? Infinity, firstTime(edge)));
      result.nodes.sort((a, b) => (nodeTimes.get(a.id) ?? Infinity) - (nodeTimes.get(b.id) ?? Infinity) || compareId(a, b));
    }
    return { nodes: result.nodes, edges: result.edges, roles, hops, focalId, hiddenCount: originals.length - shownOriginals.size, unknownUsdCount };
  }

  function layout(view) {
    const labels = ['1. Sumber / Origin', '2. Wallet Awal (Hop 0)', '3. Perantara / Layering (Hop 1)', '4. Titik Akhir / Target Tindakan'];
    const columns = labels.map((label, index) => ({ index, label, x: 145 + index * 280 }));
    const positions = new Map(), counts = [0, 0, 0, 0];
    const canonical = value => ({ BITCOIN: 'BTC', SOL: 'SOLANA', ETH: 'ETHEREUM', ETHEREUM_ERC20: 'ETHEREUM', TRON_TRC20: 'TRON' }[value] || value);
    const explicit = new Map(view.nodes.map(node => [node.id, canonical(chain(node))]));
    const branches = new Map(view.nodes.map(node => [node.id, new Set(explicit.get(node.id) ? [explicit.get(node.id)] : [])]));
    // Off-chain receiving accounts inherit a branch only when incoming evidence identifies a unique chain.
    for (let pass = 0; pass < view.nodes.length; pass++) {
      let changed = false;
      for (const edge of view.edges) {
        if (explicit.get(edge.target) || !branches.has(edge.target)) continue;
        const target = branches.get(edge.target);
        for (const name of [...(branches.get(edge.source) || []), canonical(chain(edge))].filter(Boolean)) if (!target.has(name)) { target.add(name); changed = true; }
      }
      if (!changed) break;
    }
    const branchName = node => branches.get(node.id)?.size === 1 ? [...branches.get(node.id)][0] : '';
    const branchRank = name => ({ BTC: 0, SOLANA: 1, ETHEREUM: 2 }[name] ?? (name ? 3 : 4));
    const ordered = [...view.nodes].sort((a, b) => {
      const left = view.scores?.get(a.id) || { kind: 4, key: '', amount: 0 }, right = view.scores?.get(b.id) || { kind: 4, key: '', amount: 0 };
      const column = view.columns.get(a.id) ?? 2, otherColumn = view.columns.get(b.id) ?? 2;
      const sourceOrder = column === 0 ? (hopNumber(a.hop) ?? 0) - (hopNumber(b.hop) ?? 0) : 0;
      const leftBranch = branchName(a), rightBranch = branchName(b);
      const branchOrder = column === 0 ? 0 : branchRank(leftBranch) - branchRank(rightBranch) || leftBranch.localeCompare(rightBranch);
      return column - otherColumn || (view.roles.get(a.id) === 'cluster') - (view.roles.get(b.id) === 'cluster') || sourceOrder || branchOrder || (view.roles.get(b.id) === 'target') - (view.roles.get(a.id) === 'target') || (view.hops.get(a.id) ?? 0) - (view.hops.get(b.id) ?? 0) || left.kind - right.kind || left.key.localeCompare(right.key) || right.amount - left.amount || compareId(a, b);
    });
    for (const node of ordered) {
      const column = view.columns.get(node.id) ?? 2;
      positions.set(node.id, { x: columns[column].x, y: 128 + counts[column]++ * 194 });
    }
    return { positions, width: 1130, height: Math.max(480, 212 + (Math.max(...counts, 1) - 1) * 194), cardWidth: 210, cardHeight: 84, columns };
  }
  return { project, layout, role, amountSummary, isExchange, visualize, timeline, label };
})();
