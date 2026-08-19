/**
 * Seeded pseudorandom generator for obstacle placement.
 *
 * Obstacle placement has to come from a seed rather than Math.random so a run
 * can be replayed from its seed alone. That is what makes a bug report
 * reproducible today and server-side score verification possible later, and it
 * is far cheaper to build in now than to retrofit.
 *
 * mulberry32: 32-bit integer state, and every operation is exact in a double,
 * so it produces the same stream in a browser, in Node, and in a Python port.
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

/** A seed a human can read back off a screenshot. */
export function randomSeed() {
  return (Math.floor(Math.random() * 0x7fffffff) + 1) >>> 0;
}

/**
 * Cosmetic hash, kept away from the simulation's RNG stream on purpose.
 *
 * Version labels are drawn on the tiles, and if they drew from the same
 * generator as the gap positions then changing the art would change the
 * physics. Two obstacles with the same index and seed always get the same
 * label, without the simulation ever knowing labels exist.
 */
export function hash32(seed, index) {
  let h = (seed ^ 0x9e3779b9) >>> 0;
  h = (Math.imul(h ^ index, 0x85ebca6b) ^ (index << 5)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return (h ^ (h >>> 16)) >>> 0;
}
