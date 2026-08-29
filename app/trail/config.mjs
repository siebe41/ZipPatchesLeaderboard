/**
 * Every tuning constant for Patch Trail, in one place.
 *
 * Unlike the other games here, nothing in this simulation moves by less than
 * a full cell in a tick -- the trail advances in whole grid steps, and grid
 * steps are all a discrete game like this needs. So there is no sub-unit
 * scale: positions are plain column/row integers, and the only timing
 * question is how many *ticks* make up one step, which stays an integer by
 * construction.
 */

export const CONFIG = {
  // --- Grid ------------------------------------------------------------------
  cols: 24,
  rows: 16,
  cell: 28,             // px
  hudTop: 40,            // px of score strip above the playfield

  // --- Simulation ------------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480,

  // --- Movement ------------------------------------------------------------
  // Ticks between one grid step and the next, at level 1. Shrinks with level
  // (see moveTicks() below), floored so the game never asks for a step
  // faster than the render loop can usefully show.
  moveTicksBase: 14,
  minMoveTicks: 5,
  startLength: 4,

  // --- Timing --------------------------------------------------------------
  readyTicks: 60,
  dyingTicks: 45,
  clearTicks: 100,

  // --- Scoring -------------------------------------------------------------
  pointsPerPatch: 20,
  patchesToLevelUp: 8,
  levelClearBonus: 200,
  extraLifeAt: 500,
  extraLifeEvery: 1000,
  maxLives: 6,
  lives: 4,

  // --- Level scaling -----------------------------------------------------------
  levelSpeedPct: [100, 115, 130, 145, 160, 175, 190],

  // --- Limits both engines share ---------------------------------------------
  // A turn is recorded only on a direction change, so a trace this size
  // covers thousands of direction changes across the full twelve minutes.
  maxInputTrace: 8000,
  absoluteMaxTicks: 120 * 60 * 12,
  tailTicks: 120 * 20,
  maxScore: 10000000,

  // Bounded retries before falling back to an exhaustive scan when picking a
  // cell for the next patch -- see spawnPatch() in sim.mjs. Both engines
  // must exhaust the same number of draws from the generator before falling
  // back, or the stream desyncs.
  patchSpawnRetries: 60,

  // --- Presentation only -------------------------------------------------------
  bestKey: 'trail.best',
  playerKey: 'trail.player',
  muteKey: 'trail.muted',
};

/** Floor division, named so the Python port has an obvious counterpart. */
export function fdiv(a, b) {
  return Math.floor(a / b);
}

/** The speed percent a level plays at. */
export function tierSpeedPct(level) {
  const i = Math.min(Math.max(level - 1, 0), CONFIG.levelSpeedPct.length - 1);
  return CONFIG.levelSpeedPct[i];
}

/** Ticks per grid step at a level: shorter (faster) as the tier climbs. */
export function moveTicks(level) {
  return Math.max(CONFIG.minMoveTicks,
    fdiv(CONFIG.moveTicksBase * 100, tierSpeedPct(level)));
}
