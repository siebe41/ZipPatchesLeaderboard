/**
 * Every tuning constant for PatchDefender, in one place.
 *
 * On units. Positions and speeds are integers in sub-units, and one pixel is
 * `unit` of them -- the server replays every submitted run in Python, and
 * integer arithmetic is the only kind two languages agree on without
 * argument. Every division floors.
 *
 * On this game's trace encoding. Every other game here packs an action into
 * `tick * 4 + action`, because the whole input is a handful of enum values.
 * This game's only input is "aim here", which is a point, not an enum -- so
 * the trace is a flat list of `[tick, x, y]` triplets instead of one packed
 * integer per event. It is still one list of integers, still read the same
 * way in order; there just happen to be three per event instead of one.
 */

export const CONFIG = {
  // --- Canvas and world ---------------------------------------------------
  width: 720,
  height: 480,
  unit: 64,
  hudTop: 40,
  groundY: 460,           // px; a missile below this has landed

  // --- Simulation ------------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480,

  // --- Endpoints -----------------------------------------------------------
  endpointCount: 5,
  endpointHalfW: 22,       // px, both collision and drawing
  endpointLabels: ['WORKSTATION', 'LAPTOP', 'SERVER', 'LAPTOP', 'WORKSTATION'],

  // --- The silo and its interceptors ----------------------------------------
  siloX: 360,               // px, centre of the world
  siloY: 470,
  interceptorSpeedSu: 340,   // sub-units/tick
  interceptorHalfW: 3,       // px
  fireCooldownTicks: 18,
  maxInterceptors: 6,
  blastRadiusPx: 34,
  blastTicks: 26,            // ticks the blast stays live and lethal

  // --- Zero-days -------------------------------------------------------------
  missileSpeedMinSu: { 0: 22 },
  missileSpeedMaxSu: { 0: 42 },
  missileHalfW: 3,
  missilesBase: 6,
  missilesPerLevel: 2,
  missilesCap: 20,
  spawnStaggerTicks: 600,     // ticks of jitter before a missile starts falling

  // --- Timing --------------------------------------------------------------
  readyTicks: 90,
  clearTicks: 120,

  // --- Scoring -------------------------------------------------------------
  missilePoints: 25,
  endpointBonus: 100,         // per endpoint still alive when a wave clears
  extraEndpointAt: 1200,
  extraEndpointEvery: 2000,

  // --- Level scaling -----------------------------------------------------------
  levelSpeedPct: [100, 118, 136, 154, 172, 190, 210],

  // --- Limits both engines share ---------------------------------------------
  // Three integers per shot rather than one, so the ceiling here is a count
  // of *shots*, not of raw integers -- the trace itself runs to three times
  // this many entries.
  maxShots: 4000,
  absoluteMaxTicks: 120 * 60 * 12,
  tailTicks: 120 * 15,
  maxScore: 10000000,

  // --- Presentation only -------------------------------------------------------
  bestKey: 'defender.best',
  playerKey: 'defender.player',
  muteKey: 'defender.muted',
};

export function fdiv(a, b) {
  return Math.floor(a / b);
}

export function tierSpeedPct(level) {
  const i = Math.min(Math.max(level - 1, 0), CONFIG.levelSpeedPct.length - 1);
  return CONFIG.levelSpeedPct[i];
}

/** The x, in px, endpoint i sits at -- evenly spaced with a margin at each
 * edge. Shared by the sim (targeting and hit tests) and the renderer
 * (drawing), so it lives wherever both already import from. */
export function endpointX(i) {
  return fdiv(CONFIG.width * (i + 1), CONFIG.endpointCount + 1);
}
