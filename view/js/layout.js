/**
 * The force layout: our driver, morpho's engine.
 *
 * DERIVED, not copied -- `NOTICE` says so. The parameter names come from
 * morpho's `js/force_layout.js` because they name the engine's own knobs; the
 * code below is written against the exported functions in
 * `graphs_engine/src/main.c`, which is the file that *is* copied unchanged.
 *
 * **Seeded, because the same corpus must draw the same picture twice (R7).**
 * Without that, nobody can tell a change in the picture caused by the corpus
 * from one caused by chance, and every later checkpoint that shows freshness
 * or change would be unreadable.
 *
 * **Capacity is an argument, never a constant (R8).** The engine allocates in
 * `init()` and never grows. Writing past the end of a typed view throws
 * nothing in JavaScript -- the value is silently dropped -- so a graph larger
 * than the buffers would come back with the last nodes at position zero, with
 * no error anywhere. So the size is computed from the graph, and a graph that
 * still does not fit is refused rather than half drawn.
 */
const compiled = new Map();

async function bytesOf(wasmUrl) {
  if (typeof window === "undefined") {
    const fs = await import("node:fs/promises");
    return fs.readFile(wasmUrl);
  }
  const response = await fetch(wasmUrl);
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${wasmUrl}`);
  return response.arrayBuffer();
}

/** Compiled once per URL: compiling per frame measured one frame in 25 s. */
export async function loadEngine(wasmUrl, { maxPoints, maxLinks }) {
  const key = String(wasmUrl);
  if (!compiled.has(key)) compiled.set(key, WebAssembly.compile(await bytesOf(wasmUrl)));
  const instance = await WebAssembly.instantiate(await compiled.get(key), {});
  instance.exports.init(maxPoints, maxPoints, maxLinks);
  return instance.exports;
}

/** A small, fast, well-known PRNG. Same seed, same sequence, every run. */
export function mulberry32(seed) {
  return () => {
    seed |= 0;
    seed = seed + 0x6d2b79f5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

export const defaults = {
  seed: 1337,
  steps: 400,
  repulsionForce: 0.05,
  wireForce: 1.0,
  velocityDecay: 0.05,
  theta: 0.5,
  linkDistance: 2.0,
  maxDist: 80,
  maxSpeed: 1.0,
  gravity: 0.1,
  leafSize: 64,
  maxLevel: 10,
};

function buffers(wasm) {
  const view = (name, Type) => new Type(
    wasm.memory.buffer,
    wasm[`_get_${name}`](),
    wasm[`_len_${name}__${Type === Int32Array ? "int" : "float"}`](),
  );
  return {
    points: view("points", Float32Array),
    vel: view("vel", Float32Array),
    links: view("links", Int32Array),
    weights: view("link_weights", Float32Array),
  };
}

export async function layout(graph, wasmUrl, options = {}) {
  const p = { ...defaults, ...options };
  const n = graph.nodes.length;
  // Capacity is an argument (R8). By default it is computed from the graph,
  // and on this engine that default cannot come up short: `alloc` grows the
  // WebAssembly memory, so asking for more simply gets more. **The check below
  // is therefore unreachable through this path, and that is worth saying** --
  // it guards a caller who pins the capacity, and the gate for it pins one.
  // Left in rather than deleted because "the engine will always allocate what
  // we ask for" is a property of this engine, not of the call.
  const wasm = await loadEngine(wasmUrl, options.capacity || {
    maxPoints: Math.max(n, 1024),
    maxLinks: Math.max(graph.edges.length, 1024) * 2,
  });
  const a = buffers(wasm);
  // The refusal R8 asks for. Checked against the buffers the engine actually
  // handed back, not against the numbers we asked for: `init` clamps to what
  // was compiled in, and believing our own request is how the last nodes end
  // up at the origin with nothing said.
  if (a.points.length < n * 3 || a.links.length < graph.edges.length * 2) {
    throw new Error(`layout: the engine holds ${Math.floor(a.points.length / 3)} nodes `
      + `and ${Math.floor(a.links.length / 2)} edges; this graph has ${n} and `
      + `${graph.edges.length}. Nothing is drawn rather than the last nodes silently lost`);
  }

  const index = new Map(graph.nodes.map((node, i) => [node.id, i]));
  const parent = new Int32Array(n).fill(-1);
  const degree = new Int32Array(n);
  graph.edges.forEach((edge, e) => {
    const from = index.get(edge.from);
    const to = index.get(edge.to);
    a.links[2 * e] = from;
    a.links[2 * e + 1] = to;
    parent[to] = from;
    degree[from]++;
    degree[to]++;
  });
  graph.edges.forEach((edge, e) => {
    a.weights[e] = 1 / (degree[index.get(edge.from)] + degree[index.get(edge.to)]);
  });

  // Children start near their parent rather than anywhere: the tree is the
  // structure being drawn, and a random start throws it away before the first
  // step and spends the whole run getting back to it.
  const random = mulberry32(p.seed);
  for (let i = 0; i < n; i++) {
    const base = parent[i] < 0 ? 0 : parent[i] * 3;
    a.points[i * 3] = (parent[i] < 0 ? 0 : a.points[base]) + (random() - 0.5) * 2;
    a.points[i * 3 + 1] = (parent[i] < 0 ? 0 : a.points[base + 1]) + (random() - 0.5) * 2;
    a.points[i * 3 + 2] = 0;
  }

  wasm.setTheta(p.theta);
  wasm.setMaxSpeed(p.maxSpeed);
  wasm.setLinearCompensation(0);
  for (let step = 0; step < p.steps; step++) {
    wasm.linkForce(graph.edges.length, p.wireForce, p.linkDistance, n);
    const nodeCount = wasm.buildOctree(n, p.leafSize, p.maxLevel);
    wasm.calcMultibodyForce(n, nodeCount, p.maxDist);
    wasm.applyChargeForces(n, -p.repulsionForce);
    const gravity = p.gravity / Math.max(n, 16);
    for (let i = 0; i < n; i++) {
      a.vel[i * 3] -= a.points[i * 3] * gravity;
      a.vel[i * 3 + 1] -= a.points[i * 3 + 1] * gravity;
    }
    wasm.updateNodes(n, 1 - p.velocityDecay);
  }

  const x = new Float32Array(n);
  const y = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = a.points[i * 3];
    y[i] = a.points[i * 3 + 1];
  }
  return { x, y };
}
