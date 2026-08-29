/**
 * Every tuning constant for Patch Wall, in one place.
 *
 * On units. Positions and speeds are integers in sub-units, and one pixel is
 * `unit` of them -- the server replays every submitted run in Python, and
 * integer arithmetic is the only kind two languages agree on without
 * argument. Every division floors, matching `a // b` in Python.
 *
 * On the AI. The opponent paddle is not a second player's input, so nothing
 * about it needs a seed: at a given level it reads the ball's position with
 * a fixed reaction delay and a fixed speed cap, both derived from the same
 * integer percent tier the player-facing values scale from. It is
 * deterministic and replays exactly, same as everything else here.
 */

export const CONFIG = {
  // --- Canvas ------------------------------------------------------------
  width: 720,             // px, the playfield only
  height: 440,
  unit: 64,                // sub-units per pixel
  hudTop: 40,              // px of score strip above the playfield

  // --- Simulation ----------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480,

  // --- Paddles ---------------------------------------------------------------
  paddleHalfW: 6,           // px
  paddleHalfH: 40,
  paddleMargin: 26,         // px in from the edge, centre of the paddle
  paddleSpeedSu: 50,        // sub-units/tick, the player's paddle

  // --- The ball --------------------------------------------------------------
  ballHalf: 6,               // px, square hitbox
  ballBaseSpeedSu: 60,       // sub-units/tick, at serve, before level scaling
  ballSpeedIncrementSu: 4,   // added to |vx| on every paddle hit
  ballMaxSpeedSu: 160,
  serveVyRange: 30,          // vy at serve is uniform in [-range, range]
  maxSpinSu: 90,             // vy at the paddle's own edge, replacing vy on a hit

  // --- The opponent ------------------------------------------------------------
  aiBaseSpeedSu: 44,
  aiReactBaseTicks: 20,      // ticks between the AI reading the ball's position

  // --- Timing --------------------------------------------------------------
  readyTicks: 90,            // ticks of "GET READY" before the first serve
  serveDelayTicks: 60,       // ticks the ball waits, centred, before a serve
  dyingTicks: 45,            // ticks spent on a lost point before re-serving
  clearTicks: 120,           // ticks of celebration on a level win

  // --- Scoring -------------------------------------------------------------
  rallyPoints: 5,            // every return the player lands
  aiMissPoints: 100,         // every time the ball gets past the opponent
  missesToLevelUp: 3,
  extraLifeAt: 1000,
  extraLifeEvery: 2000,
  maxLives: 6,
  lives: 4,

  // --- Level scaling -----------------------------------------------------------
  // Percent applied to the AI's speed and, inversely, its reaction delay
  // (see aiReactTicks()). The player's own paddle speed never scales: control
  // should stay predictable from level to level, so all of the escalation
  // comes from the opponent getting sharper, not from the player's own
  // paddle changing under them.
  levelSpeedPct: [100, 112, 125, 138, 150, 162, 175],

  // --- Limits both engines share ---------------------------------------------
  maxInputTrace: 8000,
  absoluteMaxTicks: 120 * 60 * 12,
  tailTicks: 120 * 30,
  maxScore: 10000000,

  // --- Presentation only -------------------------------------------------------
  bestKey: 'wall.best',
  playerKey: 'wall.player',
  muteKey: 'wall.muted',
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

/** The AI's reaction delay at a level: shorter as the tier climbs. */
export function aiReactTicks(level) {
  return Math.max(2, fdiv(CONFIG.aiReactBaseTicks * 100, tierSpeedPct(level)));
}
