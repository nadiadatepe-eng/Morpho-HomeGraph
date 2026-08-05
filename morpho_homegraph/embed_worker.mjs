/**
 * The embedding worker: one process, many chunks, JSON lines both ways.
 *
 * The library is JavaScript and the package is Python, so something has to
 * cross the line. It crosses **once per run, not once per chunk** (FASIT-cp9
 * R1): model load is 0.96 s fixed per process (M-3), and a process per chunk
 * would put that in front of every one of them -- five minutes of work turned
 * into hours.
 *
 * Protocol, deliberately the dullest thing that works:
 *   stdout, once at startup:  {"ready":true,"model":...,"dim":384}
 *   stdin, one line per chunk: a JSON string
 *   stdout, one line per chunk: {"vector":[...]} or {"error":"..."}
 * A refusal is a line on stdout too, so the reader never has to decide
 * between waiting for stdout and waiting for stderr.
 *
 * **Nothing is downloaded.** `allowRemoteModels = false` makes that a
 * guarantee instead of a hope: a missing model fails here, loudly, rather
 * than quietly fetching 130 MB from the network.
 *
 * **There is no batching and no group size** -- M-3b measured groups of 32 at
 * -40 % speed and 5x memory on both trees at once. The chunks arrive one per
 * line and are embedded one at a time, and that is the measurement, not taste.
 */
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';

const MODULES = process.env.MHG_NODE_MODULES;
const MODELS = process.env.MHG_MODELS;
const MODEL = process.env.MHG_MODEL;

function fail(message) {
  process.stdout.write(JSON.stringify({ error: message }) + '\n');
  process.exit(3);
}

if (!MODULES || !MODELS || !MODEL) {
  fail('MHG_NODE_MODULES, MHG_MODELS and MHG_MODEL must all be set');
}

let pipeline, env;
try {
  const require = createRequire(MODULES + '/');
  ({ pipeline, env } = await import(require.resolve('@xenova/transformers')));
} catch (err) {
  fail(`no @xenova/transformers under ${MODULES}: ${err.message}`);
}

env.localModelPath = MODELS;
env.cacheDir = MODELS;
env.allowRemoteModels = false;

let embed;
try {
  embed = await pipeline('feature-extraction', MODEL, { quantized: true });
} catch (err) {
  fail(`the model ${MODEL} would not load from ${MODELS}: ${err.message}`);
}

// The dimension is asked of the model, never assumed: it is what the store
// records and what a second model would differ in, and a constant here would
// be this file's opinion rather than the model's answer.
const probe = await embed('x', { pooling: 'mean', normalize: true });
process.stdout.write(
  JSON.stringify({ ready: true, model: MODEL, dim: probe.data.length }) + '\n');

for await (const line of createInterface({ input: process.stdin })) {
  if (!line) continue;
  try {
    const output = await embed(JSON.parse(line),
                              { pooling: 'mean', normalize: true });
    process.stdout.write(
      JSON.stringify({ vector: Array.from(output.data) }) + '\n');
  } catch (err) {
    process.stdout.write(JSON.stringify({ error: err.message }) + '\n');
  }
}
