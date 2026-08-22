/**
 * Seeded pseudorandom generator.
 *
 * The simulation uses this for the decisions a wave does not dictate: which bug
 * peels out of the formation next, how long it waits before it does, which way
 * its dive leans, and whether it fires on the way past. Everything else is a
 * consequence of the wave number and the player's inputs, so a run replays from
 * its seed and its input trace alone. That is what makes a bug report
 * reproducible and server-side verification possible.
 *
 * mulberry32: 32-bit integer state, every operation exact in a double, so it
 * produces the same stream in a browser, in Node, and in a Python port. This is
 * the same generator PatchMan and Flappy Duck use, deliberately -- a third
 * variant would be a third thing to keep in step for no benefit.
 */
export function makeRng(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Draw an integer in [0, n).
 *
 * The simulation only ever wants whole numbers out of the generator, so the
 * conversion lives in one place. The Python port then has one thing to match
 * rather than one per call site.
 */
export function rngInt(next, n) {
  return Math.floor(next() * n) % n;
}

/** A seed a human can read back off a screenshot. */
export function randomSeed() {
  return (Math.floor(Math.random() * 0x7fffffff) + 1) >>> 0;
}

/**
 * Cosmetic hash, kept away from the simulation's generator on purpose.
 *
 * The starfield and the CVE numbers stencilled on the bug carapaces come from
 * here. If they came from the simulation's stream instead, changing the artwork
 * would change which bug dived, which is the kind of coupling that only shows
 * up as an unexplained parity failure months later.
 */
export function hash32(seed, index) {
  let h = (seed ^ 0x9e3779b9) >>> 0;
  h = (Math.imul(h ^ index, 0x85ebca6b) ^ (index << 5)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return (h ^ (h >>> 16)) >>> 0;
}
