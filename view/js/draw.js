/**
 * The page: load the graph, lay it out once, draw it, and name what is under
 * the pointer.
 *
 * **Not one HTML string anywhere (R6).** A filename is arbitrary bytes off a
 * disk -- `<script>alert(1)</script>.md` is a legal filename, and this page
 * receives it exactly as it is on disk because the exporter does not sanitise.
 * Everything a name touches goes through `textContent` or the canvas, and
 * neither of those can be talked into running it. The protection is the
 * *absence* of a markup path, which is why `tests/test_cp11.py` gate 14 reads
 * this file for one and there is nothing here to keep in step with an escaper.
 */
import { layout } from "./layout.js";

const COLOURS = { dir: "#4a6fa5", bucket: "#8a8f98", file: "#c56a3d" };
const RADIUS = { dir: 4, bucket: 3, file: 2.5 };

// CP-12: a file is drawn by its freshness, and the four states are the ones
// `freshness.py` computes -- not a scale invented here. A colour that cannot
// be traced back to a value in `data.json` is a colour nobody can check.
const STATE_COLOURS = {
  fresh: "#3f8f4f",        // read, and matching what the catalogue last saw
  stale: "#d08b1c",        // the file changed after we read it
  unread: "#9a9a9a",       // binary, too large, or unreadable
  unembedded: "#6a5acd",   // read and current, but no vector for its hash
};
const STATE_TEXT = {
  fresh: "read, and current as of the catalogue",
  stale: "changed since we read it",
  unread: "never read: binary, too large or unreadable",
  unembedded: "read, but not embedded",
};

const canvas = document.getElementById("graph");
const context = canvas.getContext("2d");
const status = document.getElementById("status");
const hover = document.getElementById("hover");

function say(text) {
  status.textContent = text;
}

/** The same wording `freshness.human()` uses, so the two agree on sight. */
function human(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 0) return "from the future (clocks disagree)";
  if (seconds < 90) return `${Math.floor(seconds)} s`;
  if (seconds < 90 * 60) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 48 * 3600) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
}

/**
 * The key, built one element at a time (CP-11 R6 -- no markup path anywhere).
 * Only the states this graph actually contains: a legend that lists colours
 * the picture does not use teaches the reader to look for something that is
 * not there.
 */
function legend(graph) {
  const key = document.getElementById("legend");
  const present = new Set(graph.nodes.filter((n) => n.kind === "file")
                                     .map((n) => n.state));
  for (const state of ["fresh", "stale", "unread", "unembedded"]) {
    if (!present.has(state)) continue;
    const row = document.createElement("div");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = STATE_COLOURS[state];
    const label = document.createElement("span");
    const count = graph.nodes.filter((n) => n.state === state).length;
    label.textContent = `${state} (${count}) — ${STATE_TEXT[state]}`;
    row.append(swatch, label);
    key.append(row);
  }
}

async function main() {
  say("loading…");
  const response = await fetch("./data.json");
  if (!response.ok) throw new Error(`data.json: HTTP ${response.status}`);
  const graph = await response.json();

  say(`laying out ${graph.nodes.length} nodes…`);
  const positions = await layout(graph, new URL("../graphs_engine/main.wasm", import.meta.url));
  const index = new Map(graph.nodes.map((node, i) => [node.id, i]));

  // The extent decides the scale, and it is computed rather than assumed: a
  // fixed zoom draws one corpus and clips the next.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < graph.nodes.length; i++) {
    minX = Math.min(minX, positions.x[i]); maxX = Math.max(maxX, positions.x[i]);
    minY = Math.min(minY, positions.y[i]); maxY = Math.max(maxY, positions.y[i]);
  }

  let scale = 1, offsetX = 0, offsetY = 0;
  function fit() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.floor(rect.width * devicePixelRatio);
    canvas.height = Math.floor(rect.height * devicePixelRatio);
    const pad = 24 * devicePixelRatio;
    scale = Math.min((canvas.width - 2 * pad) / Math.max(maxX - minX, 1e-6),
                     (canvas.height - 2 * pad) / Math.max(maxY - minY, 1e-6));
    offsetX = pad - minX * scale;
    offsetY = pad - minY * scale;
  }
  const at = (i) => [positions.x[i] * scale + offsetX, positions.y[i] * scale + offsetY];

  function draw() {
    fit();
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.lineWidth = Math.max(1, devicePixelRatio * 0.6);
    context.strokeStyle = "#00000022";
    context.beginPath();
    for (const edge of graph.edges) {
      const from = index.get(edge.from);
      const to = index.get(edge.to);
      if (from === undefined || to === undefined) continue;
      const [x1, y1] = at(from);
      const [x2, y2] = at(to);
      context.moveTo(x1, y1);
      context.lineTo(x2, y2);
    }
    context.stroke();
    graph.nodes.forEach((node, i) => {
      const [x, y] = at(i);
      context.fillStyle = node.kind === "file"
        ? (STATE_COLOURS[node.state] || COLOURS.file)
        : (COLOURS[node.kind] || "#333");
      context.beginPath();
      context.arc(x, y, (RADIUS[node.kind] || 2) * devicePixelRatio, 0, Math.PI * 2);
      context.fill();
    });
  }

  function nearest(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const px = (clientX - rect.left) * devicePixelRatio;
    const py = (clientY - rect.top) * devicePixelRatio;
    let best = -1, bestDistance = 18 * devicePixelRatio;
    graph.nodes.forEach((node, i) => {
      const [x, y] = at(i);
      const distance = Math.hypot(x - px, y - py);
      if (distance < bestDistance) { bestDistance = distance; best = i; }
    });
    return best;
  }

  canvas.addEventListener("mousemove", (event) => {
    const i = nearest(event.clientX, event.clientY);
    if (i < 0) { hover.hidden = true; return; }
    const node = graph.nodes[i];
    // textContent, never markup: this is the line that makes a filename
    // containing a script tag a filename containing a script tag.
    // The bucket separator is NUL, the one byte a POSIX filename cannot
    // contain -- shown as an arrow, so a bucket reads "folder > type".
    hover.textContent = `${node.kind}  ${node.path.replaceAll("\u0000", " > ")}`;
    hover.hidden = false;
    hover.style.left = `${event.clientX + 12}px`;
    hover.style.top = `${event.clientY + 12}px`;
  });
  canvas.addEventListener("mouseleave", () => { hover.hidden = true; });
  addEventListener("resize", draw);

  legend(graph);
  draw();
  const kinds = { dir: 0, file: 0, bucket: 0 };
  for (const node of graph.nodes) kinds[node.kind] = (kinds[node.kind] || 0) + 1;
  const ages = Object.entries(graph.ages || {})
    .map(([name, seconds]) => `${name} ${human(seconds)}`).join(", ");
  say(`${kinds.dir} folders, ${kinds.file} files, ${kinds.bucket} type buckets`
      + (ages ? ` — layers: ${ages}` : ""));
}

main().catch((error) => { say(`could not draw: ${error.message}`); });
