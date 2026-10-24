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

  function project(graph, { focus = true } = {}) {
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
    if (focus) for (let column = 0; column < 4; column++) {
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
  return { project, layout, role, amountSummary, isExchange };
})();
