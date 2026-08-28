/**
 * Seeded pseudorandom generator.
 *
 * The simulation uses this for the one thing the level number does not
 * dictate: the phase each lane's traffic starts at, so two lanes never look
 * synchronised. Everything else -- lane speed, direction, hop timing -- is a
 * consequence of the level and the player's inputs, so a run replays from its
 * seed and its input trace alone. That is what makes a bug report
 * reproducible and server-side verification possible.
 *
 * mulberry32: 32-bit integer state, every operation exact in a double, so it
 * produces the same stream in a browser, in Node, and in a Python port. This
 * is the same generator every other game here uses, deliberately -- a second
 * variant would be a second thing to keep in step for no benefit.
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
