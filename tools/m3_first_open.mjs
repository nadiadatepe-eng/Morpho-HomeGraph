/**
 * M-3: what first-open vectorisation costs on a real code tree.
 *
 * The plan, the parameters and the threshold that would change the design are
 * in `TODO.md` under "M-3 måleplan", written before this ran. Read them first:
 * a chunks-per-second number is meaningless without the chunk size that
 * produced it.
 *
 * Two costs, because they scale differently and only one of them is paid per
 * project: **model load** is fixed per process -- the service pays it once at
 * startup, a one-shot CLI pays it every time -- and **embedding** is per chunk.
 * Reporting one total would hide which of the two a slow first open is.
 *
 * Nothing is installed and nothing is downloaded: the library and the model
 * are already on disk (see `MODULES` below). `env.allowRemoteModels = false`
 * makes that a guarantee rather than a hope -- if the cache is missing, this
 * fails instead of quietly fetching 130 MB and reporting the wrong number.
 *
 * `--batch N` runs M-3b: the same measurement with chunks sent in groups of N
 * instead of one at a time. Batching is the one obvious lever, and it is a
 * separate run with one variable changed rather than an edit to the first
 * number -- a measurement that moves two things explains neither.
 *
 *     node tools/m3_first_open.mjs <dir> [<dir> ...]
 *     node tools/m3_first_open.mjs --batch 32 <dir> [<dir> ...]
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';
import { createRequire } from 'node:module';

const MODULES = '/home/nadi/.claude/tools/memory-search/node_modules';
const MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';

// From the plan. Changing any of these invalidates comparison with the number
// recorded in TODO.md, which is why they are constants and not flags.
const CHUNK = 1000;
const OVERLAP = 100;
const MAX_BYTES = 1024 * 1024;
const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', '__pycache__',
                           '.mypy_cache', '.pytest_cache', 'dist', 'build']);

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) yield* walk(join(dir, entry.name));
    } else if (entry.isFile()) {
      yield join(dir, entry.name);
    }
  }
}

/** Text of `path`, or null when it is binary, empty or over the ceiling. */
function readText(path) {
  const size = statSync(path).size;
  if (size === 0 || size > MAX_BYTES) return null;
  const buf = readFileSync(path);
  // NUL anywhere in the first 8k is the binary test. Cheap, and wrong only for
  // text files that contain a NUL, which are not text files.
  if (buf.subarray(0, 8192).includes(0)) return null;
  return buf.toString('utf8');
}

function chunksOf(text) {
  const out = [];
  const step = CHUNK - OVERLAP;
  for (let i = 0; i < text.length; i += step) {
    const piece = text.slice(i, i + CHUNK).trim();
    if (piece) out.push(piece);
    if (i + CHUNK >= text.length) break;
  }
  return out;
}

const argv = process.argv.slice(2);
let batch = 1;
const flag = argv.indexOf('--batch');
if (flag !== -1) {
  batch = Number(argv[flag + 1]);
  if (!Number.isInteger(batch) || batch < 1) {
    console.error('--batch takes a positive integer');
    process.exit(2);
  }
  argv.splice(flag, 2);
}
const dirs = argv;
if (!dirs.length) {
  console.error('usage: node tools/m3_first_open.mjs [--batch N] <dir> [<dir> ...]');
  process.exit(2);
}

const require = createRequire(MODULES + '/');
const { pipeline, env } = await import(
  require.resolve('@xenova/transformers'));
env.localModelPath = MODULES + '/@xenova/transformers/.cache';
env.cacheDir = MODULES + '/@xenova/transformers/.cache';
env.allowRemoteModels = false;

const loadStart = performance.now();
const embed = await pipeline('feature-extraction', MODEL, { quantized: true });
const loadMs = performance.now() - loadStart;
console.log(`model load        ${(loadMs / 1000).toFixed(2)} s  (${MODEL}, quantized)`);
console.log(`chunking          ${CHUNK} chars, ${OVERLAP} overlap, ${MAX_BYTES / 1024} kB ceiling`);
console.log(`batch             ${batch}${batch === 1 ? ' (M-3: one at a time)' : ' (M-3b)'}\n`);

for (const dir of dirs) {
  let files = 0, skipped = 0, bytes = 0;
  const chunks = [];
  for (const path of walk(dir)) {
    const text = readText(path);
    if (text === null) { skipped++; continue; }
    files++;
    bytes += Buffer.byteLength(text);
    chunks.push(...chunksOf(text));
  }

  // Peak, not final: RSS after the run has already had the garbage collector
  // at it, and the number that decides whether a batch size is usable is the
  // high-water mark during it.
  let rss = 0;
  const watch = setInterval(() => {
    rss = Math.max(rss, process.memoryUsage().rss / 1e6);
  }, 250);

  const start = performance.now();
  for (let i = 0; i < chunks.length; i += batch) {
    const group = chunks.slice(i, i + batch);
    await embed(batch === 1 ? group[0] : group,
                { pooling: 'mean', normalize: true });
  }
  const ms = performance.now() - start;
  clearInterval(watch);
  rss = Math.max(rss, process.memoryUsage().rss / 1e6);

  console.log(dir);
  console.log(`  read            ${files} files (${skipped} skipped), ${(bytes / 1e6).toFixed(1)} MB`);
  console.log(`  chunks          ${chunks.length}`);
  console.log(`  embed           ${(ms / 1000).toFixed(2)} s  ->  ${(chunks.length / (ms / 1000)).toFixed(1)} chunks/s`);
  console.log(`  first open      ${((loadMs + ms) / 1000).toFixed(2)} s  (model load + embed)`);
  console.log(`  vectors         ${(chunks.length * 384 * 4 / 1e6).toFixed(1)} MB at 384 dim, float32`);
  console.log(`  peak rss        ${rss.toFixed(0)} MB\n`);
}
