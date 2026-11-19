import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCollide,
  forceX,
  forceY,
} from './d3-force.js';

const layouts = new Map();
const ns = 'http://www.w3.org/2000/svg';
const svgElement = (name, attributes = {}) => {
  const el = document.createElementNS(ns, name);
  for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
  return el;
};

export function mountGraph(container, graph, key) {
  if (!container || !graph.nodes.length) return () => {};
  const svg = container.querySelector('svg');
  const layer = svg.querySelector('.live-graph-transform');
  const edgeLayer = layer.querySelector('.live-graph-edges');
  const nodeLayer = layer.querySelector('.live-graph-nodes');
  const status = container.querySelector('.live-graph-status');
  const detail = container.querySelector('.live-graph-detail');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cache = layouts.get(key) || {
    positions: new Map(),
    floating: !reducedMotion,
    focus: '',
    transform: null,
  };
  layouts.set(key, cache);
  const columns = Math.max(1, Math.ceil(Math.sqrt(graph.nodes.length * 1.6)));
  const nodes = graph.nodes.map((node, index) => {
    const previous = cache.positions.get(node.id);
    const seedX = 100 + (index % columns) * 190;
    const seedY = 75 + Math.floor(index / columns) * 150;
    return {
      ...node,
      x: previous?.x ?? seedX,
      y: previous?.y ?? seedY,
      seedX,
      seedY,
      phase: index * 1.9,
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = graph.edges
    .filter(([a, b]) => byId.has(a) && byId.has(b))
    .map(([source, target]) => ({ source, target }));
  const edgeElements = links.map((edge) => {
    const el = svgElement('path', {
      class: 'live-graph-edge',
      'marker-end': 'url(#live-graph-arrow)',
      'data-from': edge.source,
      'data-to': edge.target,
    });
    edgeLayer.append(el);
    return el;
  });
  const nodeElements = new Map(
    nodes.map((node) => {
      const group = svgElement('g', {
        class: 'live-graph-node',
        'data-node-id': node.id,
        role: 'button',
        tabindex: '0',
        'aria-label': node.name,
      });
      const title = svgElement('title');
      title.textContent = node.id;
      const rect = svgElement('rect', { x: -78, y: -25, width: 156, height: 50, rx: 6 });
      const label = svgElement('text', { 'text-anchor': 'middle', y: -3 });
      label.textContent = node.name.slice(0, 11);
      const id = svgElement('text', {
        'text-anchor': 'middle',
        y: 14,
        class: 'live-graph-node-id',
      });
      id.textContent = node.id.slice(0, 25);
      group.append(title, rect, label, id);
      nodeLayer.append(group);
      return [node.id, group];
    }),
  );
  let clock = 0,
    frame = 0,
    lastTick = 0,
    disposed = false,
    hovered = '',
    dragging = null;
  const simulation = forceSimulation(nodes)
    .stop()
    .alphaDecay(0.025)
    .alphaTarget(0.055)
    .velocityDecay(0.48)
    .force('charge', forceManyBody().strength(-400))
    .force(
      'links',
      forceLink(links)
        .id((node) => node.id)
        .distance(195)
        .strength(0.045),
    )
    .force('collision', forceCollide(83).strength(0.7))
    .force('x', forceX((node) => node.seedX).strength(0.022))
    .force('y', forceY((node) => node.seedY).strength(0.022))
    .force('ambient', () =>
      nodes.forEach((node) => {
        if (node.fx == null) node.vx += Math.sin(clock * 0.7 + node.phase) * 0.028;
        if (node.fy == null) node.vy += Math.cos(clock * 0.6 + node.phase) * 0.025;
      }),
    );
  simulation.tick(cache.positions.size ? 0 : 35);
  let transform = cache.transform || { x: 0, y: 0, scale: 1 };
  function applyTransform() {
    layer.setAttribute(
      'transform',
      `translate(${transform.x} ${transform.y}) scale(${transform.scale})`,
    );
    cache.transform = { ...transform };
    container.querySelector('[data-graph-control="scale"]').textContent =
      Math.round(transform.scale * 100) + '%';
  }
  function fit() {
    const minX = Math.min(...nodes.map((node) => node.x)) - 105;
    const maxX = Math.max(...nodes.map((node) => node.x)) + 105;
    const minY = Math.min(...nodes.map((node) => node.y)) - 65;
    const maxY = Math.max(...nodes.map((node) => node.y)) + 65;
    const scale = Math.min(1.4, 940 / Math.max(1, maxX - minX), 500 / Math.max(1, maxY - minY));
    transform = {
      scale,
      x: 500 - ((minX + maxX) / 2) * scale,
      y: 280 - ((minY + maxY) / 2) * scale,
    };
    applyTransform();
  }
  function paint() {
    nodes.forEach((node) => {
      nodeElements.get(node.id).setAttribute('transform', `translate(${node.x} ${node.y})`);
      cache.positions.set(node.id, { x: node.x, y: node.y });
    });
    links.forEach((edge, index) => {
      edgeElements[index].setAttribute(
        'd',
        edge.source.id === edge.target.id
          ? `M${edge.source.x + 55},${edge.source.y - 20} C${edge.source.x + 130},${edge.source.y - 100} ${edge.source.x - 70},${edge.source.y - 100} ${edge.source.x - 35},${edge.source.y - 22}`
          : `M${edge.source.x},${edge.source.y} L${edge.target.x},${edge.target.y}`,
      );
    });
  }
  function highlight() {
    const id = hovered || cache.focus;
    const neighbors = new Set(id ? [id] : []);
    links.forEach((edge) => {
      if (edge.source.id === id) neighbors.add(edge.target.id);
      if (edge.target.id === id) neighbors.add(edge.source.id);
    });
    nodes.forEach((node) => {
      const el = nodeElements.get(node.id);
      el.classList.toggle('dimmed', !!id && !neighbors.has(node.id));
      el.classList.toggle('focused', node.id === id);
      if (!dragging || dragging.node !== node) {
        node.fx = node.id === id ? node.x : null;
        node.fy = node.id === id ? node.y : null;
      }
    });
    links.forEach((edge, index) => {
      const related = edge.source.id === id || edge.target.id === id;
      edgeElements[index].classList.toggle('dimmed', !!id && !related);
      edgeElements[index].classList.toggle('related', !!id && related);
    });
    const selected = byId.get(cache.focus);
    detail.hidden = !selected;
    detail.replaceChildren();
    if (selected) {
      const heading = document.createElement('strong');
      heading.textContent = selected.name;
      const text = document.createElement('p');
      text.textContent = selected.id + ' · 点击空白处清除选择，滚轮缩放，拖动节点或画布。';
      const pre = document.createElement('pre');
      pre.className = 'live-code';
      pre.textContent = JSON.stringify(selected.object, null, 2);
      detail.append(heading, text, pre);
    }
    status.textContent = `${graph.nodes.length} / ${graph.nodes.length} 个节点 · ${graph.edges.length} 条引用${id ? ` · 1跳关联 ${neighbors.size - 1} 个对象` : ''}`;
  }
  function coordinate(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }
  function zoom(scale, point = { x: 500, y: 280 }) {
    const next = Math.max(0.04, Math.min(4, scale));
    const ratio = next / transform.scale;
    transform = {
      scale: next,
      x: point.x - (point.x - transform.x) * ratio,
      y: point.y - (point.y - transform.y) * ratio,
    };
    applyTransform();
  }
  const onWheel = (event) => {
    event.preventDefault();
    zoom(transform.scale * Math.exp(-event.deltaY * 0.0015), coordinate(event));
  };
  const onDown = (event) => {
    if (event.button !== 0) return;
    const el = event.target.closest('[data-node-id]');
    const node = el && byId.get(el.dataset.nodeId);
    const point = coordinate(event);
    dragging = {
      node,
      point,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
      transform: { ...transform },
    };
    if (node) {
      node.fx = node.x;
      node.fy = node.y;
      simulation.alpha(0.3);
    }
    svg.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const onMove = (event) => {
    if (!dragging) return;
    const point = coordinate(event);
    dragging.moved ||=
      Math.hypot(event.clientX - dragging.startX, event.clientY - dragging.startY) > 4;
    if (dragging.node) {
      dragging.node.x = dragging.node.fx = (point.x - transform.x) / transform.scale;
      dragging.node.y = dragging.node.fy = (point.y - transform.y) / transform.scale;
      paint();
    } else {
      transform = {
        ...transform,
        x: dragging.transform.x + point.x - dragging.point.x,
        y: dragging.transform.y + point.y - dragging.point.y,
      };
      applyTransform();
    }
  };
  const onUp = (event) => {
    if (!dragging) return;
    if (!dragging.moved) cache.focus = dragging.node?.id || '';
    if (dragging.node) {
      dragging.node.fx = null;
      dragging.node.fy = null;
    }
    dragging = null;
    hovered = '';
    highlight();
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  };
  const onHover = (event) => {
    if (dragging) return;
    hovered = event.target.closest('[data-node-id]')?.dataset.nodeId || '';
    highlight();
  };
  const onLeave = () => {
    if (!dragging) {
      hovered = '';
      highlight();
    }
  };
  const onKey = (event) => {
    if (['Enter', ' '].includes(event.key) && event.target.dataset.nodeId) {
      event.preventDefault();
      cache.focus = event.target.dataset.nodeId;
      hovered = '';
      highlight();
    }
  };
  const onControl = (event) => {
    const action = event.target.closest('[data-graph-control]')?.dataset.graphControl;
    if (action === 'in') zoom(transform.scale * 1.25);
    if (action === 'out') zoom(transform.scale / 1.25);
    if (action === 'fit') fit();
    if (action === 'clear') {
      cache.focus = '';
      hovered = '';
      highlight();
    }
    if (action === 'pause') {
      cache.floating = !cache.floating;
      updateFloating();
    }
  };
  function updateFloating() {
    svg.dataset.layoutState = cache.floating ? 'floating' : 'paused';
  }
  function tick(now) {
    if (disposed || !svg.isConnected) return;
    if (cache.floating && !document.hidden && now - lastTick >= 33) {
      clock += Math.min(now - (lastTick || now), 80) / 1000;
      lastTick = now;
      simulation.tick();
      paint();
    }
    frame = requestAnimationFrame(tick);
  }
  svg.addEventListener('wheel', onWheel, { passive: false });
  svg.addEventListener('pointerdown', onDown);
  svg.addEventListener('pointermove', onMove);
  svg.addEventListener('pointerup', onUp);
  svg.addEventListener('pointercancel', onUp);
  svg.addEventListener('pointerover', onHover);
  svg.addEventListener('pointerleave', onLeave);
  svg.addEventListener('keydown', onKey);
  container.addEventListener('click', onControl);
  paint();
  if (cache.transform) applyTransform();
  else fit();
  highlight();
  updateFloating();
  frame = requestAnimationFrame(tick);
  return () => {
    disposed = true;
    cancelAnimationFrame(frame);
    simulation.stop();
    svg.removeEventListener('wheel', onWheel);
    svg.removeEventListener('pointerdown', onDown);
    svg.removeEventListener('pointermove', onMove);
    svg.removeEventListener('pointerup', onUp);
    svg.removeEventListener('pointercancel', onUp);
    svg.removeEventListener('pointerover', onHover);
    svg.removeEventListener('pointerleave', onLeave);
    svg.removeEventListener('keydown', onKey);
    container.removeEventListener('click', onControl);
  };
}
