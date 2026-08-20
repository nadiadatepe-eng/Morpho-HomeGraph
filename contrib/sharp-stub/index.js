// Local override for `sharp`, adopted 2026-08-20 (reports/npm-audit-2026-08-20.md).
//
// This repository embeds text only. `sharp` is 17 MB of image processing that
// @xenova/transformers imports on the execution path but that our path never
// reaches: with it stubbed out, the 384-dim vectors are bit-identical, worst
// elementwise difference 0.0 across four inputs, measured on two independent
// trees. It is also the one advisory in the tree that actually loads (four
// libvips CVEs) and the only install-time network fetch the lockfile does not
// describe.
//
// It THROWS rather than returning undefined on purpose. A silent no-op would
// turn "someone added image input" into a wrong answer; this turns it into an
// error that names the decision.
const refuse = () => {
  throw new Error(
    "sharp is stubbed out in Morpho-HomeGraph (contrib/sharp-stub). This " +
    "repository embeds text only. Image input reached the sharp path: either " +
    "that input does not belong here, or the override in package.json must be " +
    "removed and the baseline in reports/npm-audit-2026-08-20.md re-measured."
  );
};

module.exports = refuse;
module.exports.default = refuse;
module.exports.cache = refuse;
module.exports.concurrency = refuse;
module.exports.simd = refuse;
module.exports.format = {};
module.exports.versions = {};
