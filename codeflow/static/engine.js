/**
 * Testing Tutor — GraphAnimator
 * Self-contained canvas animation engine for CFGs, DU chains,
 * dominator trees, and call graphs.
 */
class GraphAnimator {
  constructor(canvasId, graphData, options = {}) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) { console.error('Canvas not found:', canvasId); return; }
    this.ctx = this.canvas.getContext('2d');
    this.graphData = JSON.parse(JSON.stringify(graphData));
    this.opts = {
      theme: options.theme || 'light',
      layout: options.layout || 'layered',
      nodeW: options.nodeW || 130,
      nodeH: options.nodeH || 38,
      padding: options.padding || 60,
      ...options,
    };

    // Per-node and per-edge visual states
    this.nodeStates = {};   // id -> string state
    this.edgeStates = {};   // index -> string state
    this.nodeScales = {};   // id -> float (for pulse animation)
    this.nodeGlowAlpha = {};// id -> float

    this.graphData.nodes.forEach(n => {
      this.nodeStates[n.id]    = n.initialState || 'default';
      this.nodeScales[n.id]    = 1.0;
      this.nodeGlowAlpha[n.id] = 0;
    });
    this.graphData.edges.forEach((_, i) => { this.edgeStates[i] = 'default'; });

    // Positions in canvas pixels
    this.positions = {};

    // Particles: { id, t, p0, cp, p3, speed, color, radius, trailPts, onDone }
    this.particles = [];
    this._particleId = 0;

    // Step-animation queue
    this.steps       = [];  // array of { type, ... }
    this.currentStep = -1;
    this.history     = [];  // snapshot stack for stepBack
    this.isPlaying   = false;
    this.speed       = 1.0;
    this._stepTimer  = null;

    // Coverage
    this.coveredNodes = new Set();
    this.coveredEdges = new Set();
    this.coveredPrimePaths = new Set();

    // Interaction
    this.dragging      = false;
    this.dragNode      = null;
    this.dragOffset    = { x: 0, y: 0 };
    this.hoverNode     = null;
    this.dragEnabled   = true;
    this._clickCBs     = [];
    this._edgeClickCBs = [];

    // Log callback
    this._logCB = null;

    // Animation loop
    this._rafId    = null;
    this._lastTime = 0;

    // Glow animation phase
    this._glowPhase = 0;

    this._tooltip = this._createTooltip();
    this._computeLayout();
    this._setupEvents();
    this._startLoop();
  }

  // ─── Layout ──────────────────────────────────────────────────────────────

  _computeLayout() {
    const algo = this.opts.layout;
    if (algo === 'force') {
      this._layoutForce();
    } else if (algo === 'tree') {
      this._layoutTree();
    } else {
      this._layoutLayered();
    }
  }

  autoLayout(algorithm) {
    this.opts.layout = algorithm;
    this._computeLayout();
    this.draw();
  }

  setNodePositions(posMap) {
    // posMap: { id: {x, y} }  — normalised 0..1
    const W = this.canvas.width, H = this.canvas.height;
    const pad = this.opts.padding;
    Object.entries(posMap).forEach(([id, p]) => {
      this.positions[parseInt(id)] = {
        x: pad + p.x * (W - 2 * pad),
        y: pad + p.y * (H - 2 * pad),
      };
    });
    this.draw();
  }

  _layoutLayered() {
    const { nodes, edges } = this.graphData;
    if (!nodes.length) return;

    const W = this.canvas.width, H = this.canvas.height;
    const pad = this.opts.padding;

    // Build adjacency (skip back-edges for layering)
    const adj = {};
    nodes.forEach(n => { adj[n.id] = []; });
    edges.forEach(e => {
      if (e.type !== 'back-edge') adj[e.from]?.push(e.to);
    });

    // Longest path from entry to assign ranks
    const rank = {};
    const entry = nodes.find(n => n.type === 'entry') || nodes[0];
    rank[entry.id] = 0;
    const topo = this._topoSort(nodes, adj);
    topo.forEach(id => {
      (adj[id] || []).forEach(nid => {
        rank[nid] = Math.max(rank[nid] ?? 0, (rank[id] ?? 0) + 1);
      });
    });
    // Nodes not yet ranked
    nodes.forEach(n => { if (rank[n.id] === undefined) rank[n.id] = 0; });

    // Group by rank
    const byRank = {};
    nodes.forEach(n => {
      const r = rank[n.id];
      (byRank[r] = byRank[r] || []).push(n.id);
    });
    const numRanks = Math.max(...Object.keys(byRank).map(Number)) + 1;

    // Assign pixel positions
    Object.entries(byRank).forEach(([r, ids]) => {
      const y = pad + (parseInt(r) + 0.5) / numRanks * (H - 2 * pad);
      ids.forEach((id, i) => {
        const x = pad + (i + 0.5) / ids.length * (W - 2 * pad);
        this.positions[id] = { x, y };
      });
    });

    // Honour pre-computed positions if provided in data
    nodes.forEach(n => {
      if (n.x !== undefined && n.y !== undefined) {
        this.positions[n.id] = {
          x: pad + n.x * (W - 2 * pad),
          y: pad + n.y * (H - 2 * pad),
        };
      }
    });
  }

  _layoutForce() {
    const { nodes, edges } = this.graphData;
    const W = this.canvas.width, H = this.canvas.height;
    const pad = this.opts.padding;

    // Initialise random
    const pos = {};
    nodes.forEach((n, i) => {
      pos[n.id] = {
        x: pad + Math.random() * (W - 2 * pad),
        y: pad + Math.random() * (H - 2 * pad),
      };
    });

    const K = 80, iter = 200, cooling = 0.92;
    let temp = (W - 2 * pad) / 4;

    for (let it = 0; it < iter; it++) {
      const disp = {};
      nodes.forEach(n => { disp[n.id] = { x: 0, y: 0 }; });

      // Repulsion
      for (let a = 0; a < nodes.length; a++) {
        for (let b = a + 1; b < nodes.length; b++) {
          const u = nodes[a].id, v = nodes[b].id;
          const dx = pos[u].x - pos[v].x, dy = pos[u].y - pos[v].y;
          const d  = Math.max(Math.hypot(dx, dy), 1);
          const f  = K * K / d;
          disp[u].x += dx / d * f; disp[u].y += dy / d * f;
          disp[v].x -= dx / d * f; disp[v].y -= dy / d * f;
        }
      }

      // Attraction
      edges.forEach(e => {
        const dx = pos[e.from].x - pos[e.to].x;
        const dy = pos[e.from].y - pos[e.to].y;
        const d  = Math.max(Math.hypot(dx, dy), 1);
        const f  = d * d / K;
        disp[e.from].x -= dx / d * f; disp[e.from].y -= dy / d * f;
        disp[e.to].x   += dx / d * f; disp[e.to].y   += dy / d * f;
      });

      nodes.forEach(n => {
        const id = n.id;
        const d = Math.max(Math.hypot(disp[id].x, disp[id].y), 1);
        const m = Math.min(d, temp);
        pos[id].x = Math.max(pad, Math.min(W - pad, pos[id].x + disp[id].x / d * m));
        pos[id].y = Math.max(pad, Math.min(H - pad, pos[id].y + disp[id].y / d * m));
      });
      temp *= cooling;
    }

    Object.assign(this.positions, pos);
  }

  _layoutTree() {
    // Simple top-down tree layout
    const { nodes, edges } = this.graphData;
    const W = this.canvas.width, H = this.canvas.height;
    const pad = this.opts.padding;

    const children = {};
    nodes.forEach(n => { children[n.id] = []; });
    edges.forEach(e => { children[e.from]?.push(e.to); });

    const root = (nodes.find(n => n.type === 'entry') || nodes[0])?.id;
    if (root === undefined) return;

    const subtreeW = {};
    const computeW = id => {
      const ch = children[id] || [];
      if (!ch.length) { subtreeW[id] = 1; return 1; }
      const w = ch.reduce((s, c) => s + computeW(c), 0);
      subtreeW[id] = w; return w;
    };
    computeW(root);

    const depth = {};
    const queue = [{ id: root, d: 0 }];
    let maxDepth = 0;
    while (queue.length) {
      const { id, d } = queue.shift();
      depth[id] = d; maxDepth = Math.max(maxDepth, d);
      (children[id] || []).forEach(c => queue.push({ id: c, d: d + 1 }));
    }

    const xOffset = {};
    xOffset[root] = 0;
    const visited = new Set();
    const assignX = id => {
      if (visited.has(id)) return;
      visited.add(id);
      const ch = children[id] || [];
      let left = xOffset[id] - subtreeW[id] / 2;
      ch.forEach(c => {
        xOffset[c] = left + subtreeW[c] / 2;
        left += subtreeW[c];
        assignX(c);
      });
    };
    assignX(root);

    const totalW = subtreeW[root] || 1;
    nodes.forEach(n => {
      const xNorm = 0.5 + (xOffset[n.id] || 0) / totalW;
      const yNorm = maxDepth > 0 ? (depth[n.id] || 0) / maxDepth : 0.5;
      this.positions[n.id] = {
        x: pad + xNorm * (W - 2 * pad),
        y: pad + yNorm * (H - 2 * pad),
      };
    });
  }

  _topoSort(nodes, adj) {
    const visited = new Set(), order = [];
    const dfs = id => {
      if (visited.has(id)) return;
      visited.add(id);
      (adj[id] || []).forEach(dfs);
      order.unshift(id);
    };
    nodes.forEach(n => dfs(n.id));
    return order;
  }

  // ─── Render Loop ──────────────────────────────────────────────────────────

  _startLoop() {
    const loop = ts => {
      const dt = Math.min((ts - this._lastTime) / 1000, 0.1);
      this._lastTime = ts;
      this._glowPhase = (this._glowPhase + dt * 2) % (Math.PI * 2);
      this._updateParticles(dt);
      this._updateScales(dt);
      this.draw();
      this._rafId = requestAnimationFrame(loop);
    };
    this._rafId = requestAnimationFrame(loop);
  }

  destroy() {
    if (this._rafId) cancelAnimationFrame(this._rafId);
    clearTimeout(this._stepTimer);
    this._tooltip?.remove();
    this.canvas.removeEventListener('mousedown',  this._onMouseDown);
    this.canvas.removeEventListener('mousemove',  this._onMouseMove);
    this.canvas.removeEventListener('mouseup',    this._onMouseUp);
    this.canvas.removeEventListener('mouseleave', this._onMouseLeave);
    this.canvas.removeEventListener('click',      this._onClick);
  }

  // ─── Drawing ──────────────────────────────────────────────────────────────

  draw() {
    const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Background
    ctx.fillStyle = this.opts.theme === 'dark' ? '#1a1a2e' : '#f8fafc';
    ctx.fillRect(0, 0, W, H);

    // Draw edges first
    this.graphData.edges.forEach((e, i) => {
      this.drawEdge(e, this.edgeStates[i] || 'default', i);
    });

    // Draw nodes
    this.graphData.nodes.forEach(n => {
      this.drawNode(n, this.nodeStates[n.id] || 'default');
    });

    // Draw particles
    this.particles.forEach(p => this.drawParticle(p));

    // Tooltip (handled via DOM)
  }

  drawNode(node, state) {
    const pos = this.positions[node.id];
    if (!pos) return;
    const ctx = this.ctx;
    const { x, y } = pos;
    const W = this.opts.nodeW, H = this.opts.nodeH;
    const scale = this.nodeScales[node.id] || 1.0;

    const colors = this._nodeColors(node.type, state);

    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.translate(-x, -y);

    // Glow ring for active/highlighted
    const glowA = this.nodeGlowAlpha[node.id] || 0;
    if (glowA > 0.01) {
      ctx.save();
      ctx.globalAlpha = glowA * (0.5 + 0.5 * Math.sin(this._glowPhase));
      ctx.strokeStyle = colors.glow || colors.stroke;
      ctx.lineWidth = 6;
      ctx.shadowColor = colors.glow || colors.stroke;
      ctx.shadowBlur = 16;
      this._nodePath(ctx, node.type, x, y, W * 1.2, H * 1.2, 10);
      ctx.stroke();
      ctx.restore();
    }

    // Node fill
    ctx.shadowBlur = state === 'active' ? 14 : 0;
    ctx.shadowColor = colors.stroke;
    ctx.fillStyle   = colors.fill;
    ctx.strokeStyle = colors.stroke;
    ctx.lineWidth   = state === 'active' ? 2.5 : 1.5;

    this._nodePath(ctx, node.type, x, y, W, H, 8);
    ctx.fill(); ctx.stroke();
    ctx.shadowBlur = 0;

    // Badge (D / U)
    if (state === 'def' || state === 'use') {
      const badge = state === 'def' ? 'D' : 'U';
      const bColor = state === 'def' ? '#f59e0b' : '#14b8a6';
      ctx.beginPath();
      ctx.arc(x + W / 2 - 8, y - H / 2 + 8, 8, 0, Math.PI * 2);
      ctx.fillStyle = bColor;
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(badge, x + W / 2 - 8, y - H / 2 + 8);
    }

    // Label text
    ctx.fillStyle    = colors.text;
    ctx.font         = node.type === 'entry' || node.type === 'exit'
      ? 'bold 12px sans-serif' : '11px monospace';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    const label = node.label || String(node.id);
    const maxW  = W - 12;
    ctx.fillText(this._truncate(ctx, label, maxW), x, y);

    // Node id (small, top-left)
    if (node.type !== 'entry' && node.type !== 'exit') {
      ctx.fillStyle    = this.opts.theme === 'dark' ? '#64748b' : '#94a3b8';
      ctx.font         = '9px monospace';
      ctx.textAlign    = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(`n${node.id}`, x - W / 2 + 4, y - H / 2 + 3);
    }

    ctx.restore();
  }

  _nodePath(ctx, type, x, y, W, H, r) {
    ctx.beginPath();
    if (type === 'decision') {
      // Diamond
      const hw = W / 2 + 8, hh = H / 2 + 6;
      ctx.moveTo(x, y - hh);
      ctx.lineTo(x + hw, y);
      ctx.lineTo(x, y + hh);
      ctx.lineTo(x - hw, y);
      ctx.closePath();
    } else if (type === 'entry' || type === 'exit') {
      // Pill / stadium
      const hw = W * 0.38, hh = H / 2;
      ctx.moveTo(x - hw + hh, y - hh);
      ctx.arcTo(x + hw, y - hh, x + hw, y + hh, hh);
      ctx.arcTo(x + hw, y + hh, x - hw, y + hh, hh);
      ctx.arcTo(x - hw, y + hh, x - hw, y - hh, hh);
      ctx.arcTo(x - hw, y - hh, x + hw, y - hh, hh);
      ctx.closePath();
    } else {
      // Rounded rect
      const hw = W / 2, hh = H / 2;
      ctx.moveTo(x - hw + r, y - hh);
      ctx.arcTo(x + hw, y - hh, x + hw, y + hh, r);
      ctx.arcTo(x + hw, y + hh, x - hw, y + hh, r);
      ctx.arcTo(x - hw, y + hh, x - hw, y - hh, r);
      ctx.arcTo(x - hw, y - hh, x + hw, y - hh, r);
      ctx.closePath();
    }
  }

  _nodeColors(type, state) {
    const dark = this.opts.theme === 'dark';
    const palettes = {
      default:     { fill: dark ? '#1e293b' : '#ffffff', stroke: dark ? '#475569' : '#94a3b8', text: dark ? '#e2e8f0' : '#1e293b', glow: null },
      active:      { fill: dark ? '#1d4ed8' : '#3b82f6', stroke: '#60a5fa', text: '#ffffff', glow: '#93c5fd' },
      visited:     { fill: dark ? '#14532d' : '#dcfce7', stroke: '#22c55e', text: dark ? '#86efac' : '#166534', glow: null },
      highlighted: { fill: dark ? '#713f12' : '#fef3c7', stroke: '#f59e0b', text: dark ? '#fcd34d' : '#92400e', glow: '#fcd34d' },
      def:         { fill: dark ? '#78350f' : '#fffbeb', stroke: '#f59e0b', text: dark ? '#fcd34d' : '#92400e', glow: '#fcd34d' },
      use:         { fill: dark ? '#134e4a' : '#f0fdfa', stroke: '#14b8a6', text: dark ? '#5eead4' : '#134e4a', glow: '#5eead4' },
      error:       { fill: dark ? '#7f1d1d' : '#fef2f2', stroke: '#ef4444', text: dark ? '#fca5a5' : '#7f1d1d', glow: null },
    };

    const base = palettes[state] || palettes.default;

    // Type-specific stroke tints on default
    if (state === 'default') {
      if (type === 'entry')   return { ...base, stroke: '#3b82f6', fill: dark ? '#1e3a5f' : '#eff6ff' };
      if (type === 'exit')    return { ...base, stroke: '#22c55e', fill: dark ? '#14532d' : '#f0fdf4' };
      if (type === 'decision') return { ...base, stroke: '#f59e0b', fill: dark ? '#292010' : '#fffbeb' };
      if (type === 'exception') return { ...base, stroke: '#ef4444', fill: dark ? '#2a0a0a' : '#fef2f2' };
    }

    return base;
  }

  drawEdge(edge, state, edgeIndex) {
    const from = this.positions[edge.from];
    const to   = this.positions[edge.to];
    if (!from || !to) return;

    const ctx  = this.ctx;
    const dark = this.opts.theme === 'dark';

    const colors = {
      default:    dark ? '#475569' : '#94a3b8',
      active:     '#3b82f6',
      visited:    '#22c55e',
      'du-path':  '#f97316',
      killed:     '#ef4444',
      'back-edge':'#a855f7',
    };

    const color = colors[state] || colors.default;

    // Compute control point (offset perpendicular for back-edges / parallel edges)
    const { p0, cp, p3 } = this._edgeBezier(edge, from, to);

    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth   = state === 'active' ? 2.5 : 1.8;
    ctx.globalAlpha = state === 'default' ? 0.7 : 1.0;

    if (state === 'du-path') {
      ctx.setLineDash([8, 5]);
    } else if (state === 'killed') {
      ctx.setLineDash([4, 4]);
    } else {
      ctx.setLineDash([]);
    }

    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.quadraticCurveTo(cp.x, cp.y, p3.x, p3.y);
    ctx.stroke();

    // Arrowhead
    this._drawArrow(ctx, cp, p3, color);

    // Edge label
    if (edge.label) {
      const mid = this._bezierPoint(0.5, p0, cp, p3);
      ctx.globalAlpha = 0.9;
      ctx.fillStyle   = color;
      ctx.font        = 'bold 11px sans-serif';
      ctx.textAlign   = 'center';
      ctx.textBaseline = 'middle';
      const bg = dark ? '#1a1a2e' : '#f8fafc';
      ctx.fillStyle = bg;
      ctx.fillRect(mid.x - 10, mid.y - 8, 20, 16);
      ctx.fillStyle = color;
      ctx.fillText(edge.label, mid.x, mid.y);
    }

    ctx.restore();
  }

  _edgeBezier(edge, from, to) {
    const W = this.opts.nodeW / 2, H = this.opts.nodeH / 2;

    // Direction vector
    const dx = to.x - from.x, dy = to.y - from.y;
    const len = Math.max(Math.hypot(dx, dy), 1);
    const ux  = dx / len, uy = dy / len;

    // Start/end at node boundary
    const p0 = { x: from.x + ux * W, y: from.y + uy * H };
    const p3 = { x: to.x   - ux * W, y: to.y   - uy * H };

    // Back-edge: large arc
    if (edge.type === 'back-edge') {
      const mx   = (from.x + to.x) / 2;
      const my   = (from.y + to.y) / 2;
      const perp = { x: -uy, y: ux };
      const dist = Math.max(Math.hypot(dx, dy) * 0.8, 60);
      return { p0, cp: { x: mx + perp.x * dist, y: my + perp.y * dist }, p3 };
    }

    // Self-loop
    if (edge.from === edge.to) {
      return {
        p0: { x: from.x + W, y: from.y - H / 2 },
        cp: { x: from.x + W + 50, y: from.y - 40 },
        p3: { x: from.x + W, y: from.y + H / 2 },
      };
    }

    // Normal edge: slight quadratic curve
    const perp = { x: -uy, y: ux };
    const curvature = 20;
    return {
      p0,
      cp: { x: (p0.x + p3.x) / 2 + perp.x * curvature, y: (p0.y + p3.y) / 2 + perp.y * curvature },
      p3,
    };
  }

  _drawArrow(ctx, cp, p3, color) {
    // Tangent at end of quadratic Bezier
    const tx = p3.x - cp.x, ty = p3.y - cp.y;
    const len = Math.max(Math.hypot(tx, ty), 1);
    const ux  = tx / len, uy = ty / len;
    const size = 10;
    const angle = Math.PI / 6;

    ctx.save();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(p3.x, p3.y);
    ctx.lineTo(p3.x - size * (ux * Math.cos(angle) - uy * Math.sin(angle)),
               p3.y - size * (uy * Math.cos(angle) + ux * Math.sin(angle)));
    ctx.lineTo(p3.x - size * (ux * Math.cos(angle) + uy * Math.sin(angle)),
               p3.y - size * (uy * Math.cos(angle) - ux * Math.sin(angle)));
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  drawParticle(p) {
    const pos = this._bezierPoint(p.t, p.p0, p.cp, p.p3);
    const ctx = this.ctx;

    // Trail
    if (p.trailPts?.length > 1) {
      ctx.save();
      for (let i = 1; i < p.trailPts.length; i++) {
        const a = (i / p.trailPts.length) * 0.4;
        ctx.strokeStyle = p.color;
        ctx.lineWidth   = p.radius * 0.6 * (i / p.trailPts.length);
        ctx.globalAlpha = a;
        ctx.beginPath();
        ctx.moveTo(p.trailPts[i - 1].x, p.trailPts[i - 1].y);
        ctx.lineTo(p.trailPts[i].x,     p.trailPts[i].y);
        ctx.stroke();
      }
      ctx.restore();
    }

    // Outer pulse ring
    const pulse = 0.5 + 0.5 * Math.sin(this._glowPhase * 3);
    ctx.save();
    ctx.globalAlpha = 0.35 * pulse;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, p.radius * 2, 0, Math.PI * 2);
    ctx.fillStyle = p.color;
    ctx.fill();

    ctx.globalAlpha = 0.7 + 0.3 * pulse;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, p.radius, 0, Math.PI * 2);
    ctx.fillStyle = p.color;
    ctx.shadowColor = p.color;
    ctx.shadowBlur  = 8;
    ctx.fill();
    ctx.restore();
  }

  // ─── Particle Engine ─────────────────────────────────────────────────────

  _updateParticles(dt) {
    const done = [];
    this.particles.forEach((p, i) => {
      const prev = this._bezierPoint(p.t, p.p0, p.cp, p.p3);
      p.t += dt * p.speed * this.speed;

      const cur = this._bezierPoint(Math.min(p.t, 1), p.p0, p.cp, p.p3);
      p.trailPts = p.trailPts || [];
      p.trailPts.push({ ...cur });
      if (p.trailPts.length > 12) p.trailPts.shift();

      if (p.t >= 1) { p.t = 1; done.push(i); }
    });

    // Call onDone and remove finished particles (in reverse order)
    done.reverse().forEach(i => {
      const p = this.particles[i];
      p.onDone?.();
      this.particles.splice(i, 1);
    });
  }

  _updateScales(dt) {
    this.graphData.nodes.forEach(n => {
      const target = this.nodeStates[n.id] === 'active' ? 1.15 : 1.0;
      const cur = this.nodeScales[n.id] || 1.0;
      this.nodeScales[n.id] = cur + (target - cur) * Math.min(dt * 8, 1);

      const glowTarget = ['active', 'highlighted', 'def', 'use'].includes(this.nodeStates[n.id]) ? 1.0 : 0;
      const curG = this.nodeGlowAlpha[n.id] || 0;
      this.nodeGlowAlpha[n.id] = curG + (glowTarget - curG) * Math.min(dt * 6, 1);
    });
  }

  _bezierPoint(t, p0, cp, p3) {
    const mt = 1 - t;
    return {
      x: mt * mt * p0.x + 2 * mt * t * cp.x + t * t * p3.x,
      y: mt * mt * p0.y + 2 * mt * t * cp.y + t * t * p3.y,
    };
  }

  _launchParticle(fromId, toId, color, onDone) {
    const from = this.positions[fromId], to = this.positions[toId];
    if (!from || !to) { onDone?.(); return; }

    const edge = this.graphData.edges.find(e => e.from === fromId && e.to === toId);
    const { p0, cp, p3 } = edge
      ? this._edgeBezier(edge, from, to)
      : {
          p0: { ...from }, cp: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 - 30 },
          p3: { ...to },
        };

    this.particles.push({
      id: this._particleId++,
      t: 0, speed: 0.7,
      p0, cp, p3,
      color,
      radius: 7,
      trailPts: [],
      onDone,
    });
  }

  // ─── Step-based Animation API ─────────────────────────────────────────────

  playPath(nodeSequence, opts = {}) {
    this.reset();
    const color = opts.color || '#3b82f6';
    const steps = [];

    for (let i = 0; i < nodeSequence.length; i++) {
      const nodeId = nodeSequence[i];
      steps.push({ type: 'activateNode', nodeId });
      if (i > 0) {
        steps.push({ type: 'activateEdge', from: nodeSequence[i - 1], to: nodeId });
        steps.push({ type: 'particleTravel', from: nodeSequence[i - 1], to: nodeId, color });
      }
      steps.push({ type: 'visitNode', nodeId });
      if (i > 0) steps.push({ type: 'visitEdge', from: nodeSequence[i - 1], to: nodeId });
      steps.push({ type: 'log', msg: this._nodeLog(nodeId) });
    }

    this.steps = steps;
    this.currentStep = -1;
    if (!opts.manual) this._scheduleNextStep();
  }

  animateCoverage(paths) {
    this.reset();
    const steps = [];
    paths.forEach((path, pi) => {
      path.forEach((nodeId, i) => {
        steps.push({ type: 'activateNode', nodeId });
        if (i > 0) {
          steps.push({ type: 'particleTravel', from: path[i - 1], to: nodeId, color: '#3b82f6' });
          steps.push({ type: 'visitEdge', from: path[i - 1], to: nodeId });
        }
        steps.push({ type: 'visitNode', nodeId });
      });
      steps.push({ type: 'log', msg: `Path ${pi + 1} covered: [${path.join('→')}]` });
      steps.push({ type: 'pause', duration: 400 });
    });
    this.steps = steps;
    this.currentStep = -1;
    this._scheduleNextStep();
  }

  animateDuChain(defNodeId, useNodeIds) {
    const steps = [];
    steps.push({ type: 'setNodeState', nodeId: defNodeId, state: 'def' });
    useNodeIds.forEach(uid => {
      steps.push({ type: 'setNodeState', nodeId: uid, state: 'use' });
    });
    useNodeIds.forEach(uid => {
      steps.push({ type: 'particleTravel', from: defNodeId, to: uid, color: '#f97316' });
      steps.push({ type: 'markDuEdge', from: defNodeId, to: uid });
      steps.push({ type: 'log', msg: `DU-pair: def@n${defNodeId} → use@n${uid}` });
    });
    this.steps = [...this.steps, ...steps];
    if (!this.isPlaying) this._scheduleNextStep();
  }

  _scheduleNextStep() {
    if (this.currentStep >= this.steps.length - 1) {
      this.isPlaying = false;
      return;
    }
    this.isPlaying = true;
    const delay = this._stepDelay();
    this._stepTimer = setTimeout(() => this._executeStep(), delay);
  }

  _stepDelay() {
    return Math.round(350 / this.speed);
  }

  _executeStep() {
    if (this.currentStep >= this.steps.length - 1) {
      this.isPlaying = false;
      return;
    }
    this.currentStep++;
    const step = this.steps[this.currentStep];
    this._applyStep(step, () => {
      if (this.isPlaying) this._scheduleNextStep();
    });
  }

  _applyStep(step, done) {
    switch (step.type) {
      case 'activateNode':
        this._saveHistory();
        this.nodeStates[step.nodeId] = 'active';
        done();
        break;
      case 'visitNode':
        this.nodeStates[step.nodeId] = 'visited';
        this.coveredNodes.add(step.nodeId);
        this._updateCoverageCB?.();
        done();
        break;
      case 'activateEdge': {
        const ei = this._findEdgeIndex(step.from, step.to);
        if (ei >= 0) this.edgeStates[ei] = 'active';
        done();
        break;
      }
      case 'visitEdge': {
        const ei = this._findEdgeIndex(step.from, step.to);
        if (ei >= 0) {
          this.edgeStates[ei] = 'visited';
          this.coveredEdges.add(ei);
        }
        done();
        break;
      }
      case 'markDuEdge': {
        const ei = this._findEdgeIndex(step.from, step.to);
        if (ei >= 0) this.edgeStates[ei] = 'du-path';
        done();
        break;
      }
      case 'particleTravel':
        this._launchParticle(step.from, step.to, step.color, done);
        break;
      case 'setNodeState':
        this.nodeStates[step.nodeId] = step.state;
        done();
        break;
      case 'log':
        this._logCB?.(step.msg);
        done();
        break;
      case 'pause':
        setTimeout(done, step.duration || 300);
        break;
      default:
        done();
    }
  }

  _saveHistory() {
    this.history.push({
      nodeStates:  JSON.parse(JSON.stringify(this.nodeStates)),
      edgeStates:  JSON.parse(JSON.stringify(this.edgeStates)),
      coveredNodes: new Set(this.coveredNodes),
      coveredEdges: new Set(this.coveredEdges),
    });
    if (this.history.length > 200) this.history.shift();
  }

  stepForward() {
    clearTimeout(this._stepTimer);
    this._executeStep();
  }

  stepBack() {
    if (this.history.length === 0) return;
    const snap = this.history.pop();
    this.nodeStates   = snap.nodeStates;
    this.edgeStates   = snap.edgeStates;
    this.coveredNodes = snap.coveredNodes;
    this.coveredEdges = snap.coveredEdges;
    this.currentStep  = Math.max(this.currentStep - 1, -1);
    this._updateCoverageCB?.();
  }

  pause() {
    this.isPlaying = false;
    clearTimeout(this._stepTimer);
  }

  resume() {
    if (this.currentStep < this.steps.length - 1) {
      this.isPlaying = true;
      this._scheduleNextStep();
    }
  }

  setSpeed(multiplier) {
    this.speed = Math.max(0.5, Math.min(5, multiplier));
  }

  reset() {
    clearTimeout(this._stepTimer);
    this.isPlaying = false;
    this.steps = [];
    this.currentStep = -1;
    this.history = [];
    this.particles = [];
    this.coveredNodes.clear();
    this.coveredEdges.clear();
    this.graphData.nodes.forEach(n => { this.nodeStates[n.id] = 'default'; });
    this.graphData.edges.forEach((_, i) => { this.edgeStates[i] = 'default'; });
    this._updateCoverageCB?.();
  }

  // ─── Coverage Queries ─────────────────────────────────────────────────────

  getCoveredNodes()  { return [...this.coveredNodes]; }
  getCoveredEdges()  { return [...this.coveredEdges]; }
  getCoveragePercent(type = 'node') {
    if (type === 'node') {
      return this.graphData.nodes.length
        ? Math.round(this.coveredNodes.size / this.graphData.nodes.length * 100) : 0;
    }
    if (type === 'edge') {
      return this.graphData.edges.length
        ? Math.round(this.coveredEdges.size / this.graphData.edges.length * 100) : 0;
    }
    return 0;
  }

  // ─── Interaction ──────────────────────────────────────────────────────────

  onNodeClick(cb)      { this._clickCBs.push(cb); }
  onEdgeClick(cb)      { this._edgeClickCBs.push(cb); }
  onLog(cb)            { this._logCB = cb; }
  onCoverageUpdate(cb) { this._updateCoverageCB = cb; }
  enableDrag(v)        { this.dragEnabled = v; }

  _setupEvents() {
    const rect = () => this.canvas.getBoundingClientRect();
    const toCanvas = (e) => {
      const r = rect();
      const scaleX = this.canvas.width  / r.width;
      const scaleY = this.canvas.height / r.height;
      return { x: (e.clientX - r.left) * scaleX, y: (e.clientY - r.top) * scaleY };
    };

    this._onMouseDown = e => {
      const { x, y } = toCanvas(e);
      const n = this._nodeAt(x, y);
      if (n && this.dragEnabled) {
        this.dragging   = true;
        this.dragNode   = n.id;
        this.dragOffset = { x: x - this.positions[n.id].x, y: y - this.positions[n.id].y };
      }
    };

    this._onMouseMove = e => {
      const { x, y } = toCanvas(e);
      if (this.dragging && this.dragNode !== null) {
        this.positions[this.dragNode] = { x: x - this.dragOffset.x, y: y - this.dragOffset.y };
      }
      const n = this._nodeAt(x, y);
      if (n !== this.hoverNode) {
        this.hoverNode = n ? n.id : null;
        if (n) {
          this._showTooltip(e.clientX, e.clientY, n);
        } else {
          this._hideTooltip();
        }
      }
    };

    this._onMouseUp = () => {
      this.dragging = false;
      this.dragNode = null;
    };

    this._onMouseLeave = () => {
      this.dragging = false;
      this.dragNode = null;
      this._hideTooltip();
    };

    this._onClick = e => {
      const { x, y } = toCanvas(e);
      const n = this._nodeAt(x, y);
      if (n) this._clickCBs.forEach(cb => cb(n));
    };

    this.canvas.addEventListener('mousedown',  this._onMouseDown);
    this.canvas.addEventListener('mousemove',  this._onMouseMove);
    this.canvas.addEventListener('mouseup',    this._onMouseUp);
    this.canvas.addEventListener('mouseleave', this._onMouseLeave);
    this.canvas.addEventListener('click',      this._onClick);

    // Keyboard shortcuts (only when canvas is focused)
    this.canvas.setAttribute('tabindex', '0');
    this.canvas.addEventListener('keydown', e => {
      if (e.code === 'Space')       { e.preventDefault(); this.isPlaying ? this.pause() : this.resume(); }
      if (e.code === 'ArrowRight')  { e.preventDefault(); this.stepForward(); }
      if (e.code === 'ArrowLeft')   { e.preventDefault(); this.stepBack(); }
      if (e.code === 'KeyR')        { e.preventDefault(); this.reset(); }
    });
  }

  _nodeAt(x, y) {
    const W = this.opts.nodeW / 2, H = this.opts.nodeH / 2 + 8;
    for (let i = this.graphData.nodes.length - 1; i >= 0; i--) {
      const n = this.graphData.nodes[i];
      const p = this.positions[n.id];
      if (!p) continue;
      if (n.type === 'decision') {
        const hw = W + 8, hh = H + 2;
        // Diamond hit-test
        const dx = Math.abs(x - p.x) / hw, dy = Math.abs(y - p.y) / hh;
        if (dx + dy <= 1) return n;
      } else {
        if (Math.abs(x - p.x) <= W && Math.abs(y - p.y) <= H) return n;
      }
    }
    return null;
  }

  _findEdgeIndex(fromId, toId) {
    return this.graphData.edges.findIndex(e => e.from === fromId && e.to === toId);
  }

  // ─── Tooltip ──────────────────────────────────────────────────────────────

  _createTooltip() {
    const t = document.createElement('div');
    t.id = 'ga-tooltip-' + Math.random().toString(36).slice(2);
    t.style.cssText = `
      position:fixed;display:none;z-index:9999;
      background:#1e293b;color:#e2e8f0;
      font:12px monospace;padding:8px 12px;
      border-radius:6px;border:1px solid #475569;
      pointer-events:none;max-width:280px;line-height:1.6;
      box-shadow:0 4px 12px rgba(0,0,0,0.4);
    `;
    document.body.appendChild(t);
    return t;
  }

  _showTooltip(cx, cy, node) {
    const lines = [`<b>Node ${node.id}</b> [${node.type}]`];
    if (node.code) lines.push(`<span style="color:#93c5fd">${this._escHtml(node.code)}</span>`);
    if (node.vars_def?.length) lines.push(`<span style="color:#fcd34d">def: ${node.vars_def.join(', ')}</span>`);
    if (node.vars_use?.length) lines.push(`<span style="color:#5eead4">use: ${node.vars_use.join(', ')}</span>`);
    if (node.lines?.length)    lines.push(`lines: ${node.lines.join('-')}`);

    this._tooltip.innerHTML = lines.join('<br>');
    this._tooltip.style.display = 'block';
    this._tooltip.style.left = (cx + 14) + 'px';
    this._tooltip.style.top  = (cy + 14) + 'px';
  }

  _hideTooltip() {
    this._tooltip.style.display = 'none';
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  _nodeLog(id) {
    const n = this.graphData.nodes.find(x => x.id === id);
    if (!n) return `Node ${id}`;
    const parts = [`→ Node ${id} (${n.type})`];
    if (n.code) parts.push(n.code);
    if (n.vars_def?.length) parts.push(`def: ${n.vars_def.join(', ')}`);
    if (n.vars_use?.length) parts.push(`use: ${n.vars_use.join(', ')}`);
    return parts.join('  |  ');
  }

  _truncate(ctx, text, maxW) {
    if (ctx.measureText(text).width <= maxW) return text;
    while (text.length > 4 && ctx.measureText(text + '…').width > maxW) {
      text = text.slice(0, -1);
    }
    return text + '…';
  }

  _escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Highlight a set of nodes (e.g. dominator set)
  highlightNodes(ids, state = 'highlighted') {
    this._saveHistory();
    this.graphData.nodes.forEach(n => {
      if (ids.includes(n.id)) this.nodeStates[n.id] = state;
    });
  }

  setEdgeState(fromId, toId, state) {
    const ei = this._findEdgeIndex(fromId, toId);
    if (ei >= 0) this.edgeStates[ei] = state;
  }
}

// ─── Global helpers called from NiceGUI pages ─────────────────────────────

function cfgInit(canvasId, graphData, opts) {
  if (window._animators) window._animators[canvasId]?.destroy();
  window._animators = window._animators || {};
  const a = new GraphAnimator(canvasId, graphData, opts || {});
  window._animators[canvasId] = a;
  return a;
}

function cfgGetAnimator(canvasId) {
  return window._animators?.[canvasId];
}

/**
 * Retry until both GraphAnimator class is loaded AND the canvas element
 * exists in the DOM, then call initFn(). Safe to call from add_body_html
 * scripts where Vue may not have mounted the element yet.
 */
function cfgWhenReady(canvasId, initFn) {
  function attempt() {
    if (typeof GraphAnimator === 'undefined' || !document.getElementById(canvasId)) {
      setTimeout(attempt, 80);
      return;
    }
    initFn();
  }
  attempt();
}
