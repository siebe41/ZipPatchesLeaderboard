/**
 * Every tuning constant for Patch Sweeper, in one place.
 *
 * On units. Positions and speeds are integers in sub-units, and one pixel is
 * `unit` of them -- the server replays every submitted run in Python, and
 * integer arithmetic is the only kind two languages agree on without
 * argument. Every division floors.
 *
 * On angles. The ship's heading is never a float and never radians: it is
 * an integer step around a table-driven circle of `sinSteps`, and sine comes
 * out of a table this file builds with integer arithmetic alone -- the same
 * scheme Patchaga uses, for the same reason. `Math.sin` is not specified to
 * agree with Python's `math.sin` to the last bit, and a drone drifting for
 * twelve minutes on that disagreement is exactly the kind of bug that shows
 * up on screen in one engine and not the other.
 *
 * On speed caps. The ship's velocity is clamped on each axis independently
 * rather than by magnitude, because capping a vector's magnitude means
 * normalising it, and normalising means a square root -- the one piece of
 * arithmetic this file avoids as carefully as it avoids sine and cosine.
 */

// --- Angles ------------------------------------------------------------------

export const SIN_STEPS = 1024;
export const SIN_HALF = SIN_STEPS / 2;
export const SIN_QUARTER = SIN_STEPS / 4;
export const SIN_SCALE = 4096;

function buildSineTable() {
  const table = new Array(SIN_STEPS);
  for (let i = 0; i < SIN_STEPS; i++) {
    const j = i < SIN_HALF ? i : i - SIN_HALF;
    const p = j * (SIN_HALF - j);
    const value = Math.floor((16 * p * SIN_SCALE) / (5 * SIN_HALF * SIN_HALF - 4 * p));
    const signed = i < SIN_HALF ? value : -value;
    table[i] = signed === 0 ? 0 : signed;
  }
  return table;
}

export const SIN_TABLE = buildSineTable();

export function isin(steps) {
  return SIN_TABLE[((steps % SIN_STEPS) + SIN_STEPS) % SIN_STEPS];
}

export function icos(steps) {
  return isin(steps + SIN_QUARTER);
}

export function fdiv(a, b) {
  return Math.floor(a / b);
}

// --- The game ------------------------------------------------------------------

export const CONFIG = {
  // --- Canvas and world ---------------------------------------------------
  width: 720,
  height: 480,
  unit: 64,
  hudTop: 40,

  // --- Simulation ------------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480,

  // --- The ship --------------------------------------------------------------
  turnRateSteps: 10,        // steps/tick the heading turns
  thrustAccelSu: 6,          // sub-units/tick added to velocity while thrusting
  maxAxisSpeedSu: 320,       // cap on |vx| and |vy| independently, not on speed
  shipHalfW: 9,              // px, collision
  shipHalfH: 9,
  respawnIframeTicks: 180,   // ticks of immunity after a respawn

  // --- Patches (what the ship fires) --------------------------------------
  patchSpeedSu: 420,         // sub-units/tick, relative to the world
  patchHalfW: 3,             // px
  patchLifetimeTicks: 70,
  patchCooldownTicks: 16,
  maxPatches: 4,

  // --- Debt chunks -------------------------------------------------------------
  // Three sizes: a LEGACY_MONOLITH splits into two MODULEs, a MODULE splits
  // into two DEPENDENCYs, a DEPENDENCY is simply gone. Smaller is worth more
  // and is harder to hit, same trade the genre has always made.
  chunkHalfW: { 0: 26, 1: 15, 2: 8 },      // px, by size tier: 0 large, 1 mid, 2 small
  chunkPoints: { 0: 20, 1: 50, 2: 100 },
  chunkSpeedMinSu: { 0: 20, 1: 40, 2: 70 },
  chunkSpeedMaxSu: { 0: 55, 1: 90, 2: 140 },
  chunkSpawnPerLevel: 3,     // additional large chunks per level, on top of the base
  chunkSpawnBase: 3,

  // --- Timing --------------------------------------------------------------
  readyTicks: 90,
  dyingTicks: 60,
  clearTicks: 120,

  // --- Scoring -------------------------------------------------------------
  levelClearBonus: 500,
  extraLifeAt: 800,
  extraLifeEvery: 1500,
  maxLives: 6,
  lives: 4,

  // --- Level scaling -----------------------------------------------------------
  levelSpeedPct: [100, 115, 130, 145, 160, 175, 190],

  // --- Limits both engines share ---------------------------------------------
  // This game's trace uses three bits per event (tick * 8 + action), not the
  // two every other game here uses, because a ship needs turn, thrust and
  // fire as independent channels rather than one steer-and-fire stream.
  maxInputTrace: 10000,
  absoluteMaxTicks: 120 * 60 * 12,
  tailTicks: 120 * 20,
  maxScore: 10000000,

  // --- Presentation only -------------------------------------------------------
  bestKey: 'sweeper.best',
  playerKey: 'sweeper.player',
  muteKey: 'sweeper.muted',
};

export function tierSpeedPct(level) {
  const i = Math.min(Math.max(level - 1, 0), CONFIG.levelSpeedPct.length - 1);
  return CONFIG.levelSpeedPct[i];
}
