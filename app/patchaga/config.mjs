/**
 * Every tuning constant for Patchaga, in one place.
 *
 * A fixed shooter lives or dies on its numbers, so if a value that affects how
 * the game feels lives anywhere else in this folder, it is in the wrong place.
 *
 * On units. The simulation never uses pixels and never uses floating point for
 * position. Everything that moves holds x and y as integers in *sub-units*, and
 * one pixel is `unit` of them. That is not fussiness: the server replays every
 * submitted run in Python to decide the score, and integer arithmetic is the
 * only kind two languages agree on without argument.
 *
 * On angles. The simulation never calls Math.sin, Math.cos, Math.atan2 or
 * Math.sqrt. Those are the one part of IEEE 754 that is *not* specified to the
 * last bit, so a browser and a Python port are allowed to disagree about them,
 * and a swooping dive is exactly the kind of long integration where a one-ULP
 * disagreement becomes a bug that is on screen and off screen respectively. So
 * angles are integer steps around a circle of `sinSteps`, and sine comes out of
 * a table this file builds with integer arithmetic alone.
 *
 * On division. Both engines floor. JavaScript writes `Math.floor(a / b)` and
 * Python writes `a // b`, and those agree for negative operands too, which
 * `Math.trunc` and C-style truncation would not. Every division in the
 * simulation is a floor division for that reason.
 *
 * Rendering is the only place pixels appear. `width`, `height` and anything
 * measured in them are drawing constants.
 */

// --- Angles ---------------------------------------------------------------- #

/** Steps in a full turn. A power of two so wrapping is exact. */
export const SIN_STEPS = 1024;
export const SIN_HALF = SIN_STEPS / 2;
export const SIN_QUARTER = SIN_STEPS / 4;

/** Sine values are scaled by this, so 4096 means 1.0. */
export const SIN_SCALE = 4096;

/**
 * Bhaskara I's sine approximation, in integers.
 *
 *     sin(x) ~= 16x(pi - x) / (5pi^2 - 4x(pi - x))     for x in [0, pi]
 *
 * Expressed in steps rather than radians, pi cancels out of the ratio entirely,
 * which is the whole reason this form was chosen: the table can be built
 * without a single irrational number appearing anywhere. Every operand below is
 * a positive integer under 2^33, so the arithmetic is exact in a double and
 * identical in Python.
 *
 * It is accurate to about 0.0016, which is under one sub-unit at the speeds
 * anything here travels, and -- far more importantly -- it is wrong by exactly
 * the same amount in both engines.
 */
function buildSineTable() {
  const table = new Array(SIN_STEPS);
  for (let i = 0; i < SIN_STEPS; i++) {
    const j = i < SIN_HALF ? i : i - SIN_HALF;
    const p = j * (SIN_HALF - j);
    const value = Math.floor((16 * p * SIN_SCALE) / (5 * SIN_HALF * SIN_HALF - 4 * p));
    const signed = i < SIN_HALF ? value : -value;
    // Normalised because JavaScript has a negative zero and Python does not.
    // The two behave identically in arithmetic, but they serialise differently,
    // and a parity report that flags 0 against -0 wastes an afternoon.
    table[i] = signed === 0 ? 0 : signed;
  }
  return table;
}

export const SIN_TABLE = buildSineTable();

/** Sine of an angle in steps, scaled by SIN_SCALE. Wraps, including negatives. */
export function isin(steps) {
  return SIN_TABLE[((steps % SIN_STEPS) + SIN_STEPS) % SIN_STEPS];
}

/** Cosine of an angle in steps, scaled by SIN_SCALE. */
export function icos(steps) {
  return isin(steps + SIN_QUARTER);
}

/** Floor division, named so the Python port has an obvious counterpart. */
export function fdiv(a, b) {
  return Math.floor(a / b);
}

// --- The game -------------------------------------------------------------- #

export const CONFIG = {
  // --- Canvas and world ---------------------------------------------------
  width: 432,           // px
  height: 560,          // px
  unit: 64,             // sub-units per pixel, in both axes
  hudTop: 34,           // px of score strip above the playfield
  floorY: 540,          // px; a bug that passes this has left the screen

  // --- Simulation ---------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480, // a backgrounded tab hands back minutes; do not chase it

  // --- The duck -----------------------------------------------------------
  // 56 sub-units a tick at 120 Hz is 105 pixels a second, which crosses the
  // playfield in a shade over four seconds. Fast enough to dodge a dive that
  // has already committed, slow enough that positioning is a decision.
  duckY: 502,           // px; the duck never leaves its row
  duckSpeed: 56,        // sub-units per tick
  duckHalfW: 7,         // px, collision only. Deliberately smaller than the
  duckHalfH: 6,         // drawn duck: a near miss should read as a near miss.
  duckMargin: 14,       // px of wall the duck cannot cross

  // --- Patches (what the duck fires) --------------------------------------
  // A cap on patches in the air is the constraint the whole genre is built on:
  // it makes a missed shot cost something. But the cap only costs *time*, and
  // how much time is set by how long a patch takes to leave the screen, which
  // is a consequence of the speed rather than anything anyone chose. At the
  // original 200 a patch needed 1.36s to clear 476px, so two in the air held
  // the sustained rate to 1.6 shots a second while the cooldown below would
  // have allowed 9.2. Four presses in five did nothing at all, and playtesting
  // called that exactly what it was: unresponsive.
  //
  // So the patch is faster and the cap is one wider. A miss still costs, at
  // 0.61s instead of 1.36s, and the sustained rate is 4.8 a second.
  //
  // patchSpeed cannot go much above this. Collision is a per-tick overlap test
  // rather than a swept one, so a patch that advances more than
  // bugHalfH + patchHalfH = 14px in a tick can pass clean through a bug
  // between two tests and never register. 6.5px leaves the other half of that
  // budget for the bug's own motion closing from the opposite direction.
  patchSpeed: 416,      // sub-units per tick, upward = 6.5px/tick
  patchHalfW: 4,        // px
  patchHalfH: 6,
  maxPatches: 3,
  patchCooldown: 13,    // ticks between presses that fire

  // --- What the bugs fire -------------------------------------------------
  bugShotSpeed: 68,     // sub-units per tick
  bugShotHalfW: 3,      // px
  bugShotHalfH: 6,
  maxBugShots: 10,
  bugShotSpread: 96,    // steps either side of straight down

  // --- Formation ----------------------------------------------------------
  formCols: 10,
  formRows: 5,
  colStep: 36,          // px between columns
  rowStep: 30,          // px between rows
  formTop: 92,          // px, centre of the top row
  swayAmp: 11,          // px the formation drifts either side of centre
  swayPeriod: 540,      // ticks for a full left-right-left cycle
  breathePeriod: 900,   // ticks for the slow in-out of the columns
  breatheAmp: 3,        // px

  // --- Bug bodies ---------------------------------------------------------
  bugHalfW: 9,          // px, collision
  bugHalfH: 8,

  // --- Wave structure -----------------------------------------------------
  // Row 0 holds the rootkits, which are the only bugs that can encrypt the
  // duck's fire control. Rows 1 and 2 are weevils, rows 3 and 4 drones. 44
  // bugs, of which 4 matter.
  rootkitCols: [3, 4, 5, 6],
  entryTicks: 150,      // ticks one bug spends flying its entry path
  entryStagger: 16,     // ticks between one bug launching and the next
  entrySettle: 90,      // ticks after the last arrival before attacks begin
  readyTicks: 200,      // ticks of "WAVE n" before the entry starts
  clearTicks: 210,      // ticks of celebration after the last bug dies
  deathTicks: 200,      // ticks the duck spends exploding
  respawnTicks: 90,     // ticks of stillness after a death before control returns

  // --- Diving -------------------------------------------------------------
  diveTicks: 330,       // ticks a full dive lasts before the bug is offscreen
  diveFall: 132,        // sub-units of descent per tick, once the dive is up to speed
  diveEaseTicks: 40,    // ticks the descent takes to reach that speed
  diveSwingSpeed: 98,   // sub-units per tick at the extremes of the sideways swing
  diveSwingPeriod: 240, // ticks for one full swing left and back
  diveHomePull: 26,     // sub-units per tick a dive leans toward the duck
  diveHomeAfter: 120,   // ticks before that leaning starts
  reentryTicks: 96,     // ticks to fly from the top back into the slot
  diveGapMin: 150,      // ticks between launches, before wave scaling
  diveGapSpread: 120,   // extra random ticks on top of the minimum
  maxDiversBase: 2,     // simultaneous divers on wave 1
  maxDiversCap: 6,
  fireChance: 22,       // percent, per firing opportunity in a dive
  fireEvery: 40,        // ticks between a diving bug's firing opportunities
  // Rootkits are 4 bugs in 44, so picking a diver uniformly would show the
  // lock -- the one mechanic worth learning -- about twice a wave. After this
  // many dives without one, the next one is a rootkit if any is still parked.
  rootkitEvery: 5,

  // --- The lock -------------------------------------------------------------
  // A rootkit that reaches the duck's altitude opens a beam instead of
  // ramming. Getting caught does not cost a life: it encrypts the duck's fire
  // control, and the duck keeps flying, just unable to shoot back. The lock
  // clears itself if the timer runs out, or instantly if the rootkit holding
  // it is shot down first -- which also overclocks the fire rate for a bit,
  // so hunting down the right target is worth the risk of going in unarmed.
  beamTicks: 200,       // how long a beam stays open
  beamWindup: 46,       // ticks of beam drawn before it can catch anything
  beamHalfW: 22,        // px, the catchable half-width at the duck's row
  beamHoverY: 300,      // px the rootkit hovers at while beaming
  lockChance: 45,       // percent chance a rootkit dive is a lock attempt
  lockTicks: 300,       // ticks the duck stays encrypted before it clears itself
  overclockTicks: 360,  // ticks of halved fire cooldown after an early cure
  overclockCooldownPct: 50, // fire cooldown, as a percent of normal, while overclocked
  encryptBonus: 1000,   // points for shooting down a lock before it expires

  // --- The regression sweep (bonus wave) ----------------------------------
  // Every fourth wave nothing shoots and nothing forms up. The bugs fly a
  // pattern through the screen and leave. Hit all of them and the bonus is
  // worth more than the wave you skipped.
  sweepEvery: 4,
  sweepGroups: 5,
  sweepGroupSize: 8,
  sweepGap: 130,        // ticks between groups
  sweepTicks: 400,      // ticks one group takes to cross
  sweepPerfect: 3000,
  sweepPerBug: 120,

  // --- Scoring ------------------------------------------------------------
  // A bug is worth double once it has left the formation, because a diving bug
  // is the one that can kill you. Sitting still and clearing the back rows is
  // the safe way to play and it pays like it.
  points: {
    drone: { still: 50, diving: 100 },
    weevil: { still: 80, diving: 160 },
    rootkit: { still: 150, diving: 400 },
  },
  waveBonus: 200,       // times the wave number, on a full clear
  extraLifeAt: 20000,
  extraLifeEvery: 60000,
  maxLives: 5,
  lives: 3,

  // --- Limits both engines share ------------------------------------------
  // A trace longer than this is refused rather than replayed, so a submission
  // can never cost the server an unbounded amount of work.
  //
  // The ceiling has to clear a *legitimate* maximum run comfortably, or the
  // game goes deaf on its best players. Firing as fast as the cooldown allows
  // for the full twelve minutes is 6,646 presses on its own, and steering adds
  // a few thousand more. 20,000 leaves room for both and still replays in
  // milliseconds.
  maxInputTrace: 20000,
  absoluteMaxTicks: 120 * 60 * 12,  // twelve minutes
  tailTicks: 120 * 90,              // room for the last life to end without input
  maxScore: 10000000,

  // --- Presentation only --------------------------------------------------
  bestKey: 'patchaga.best',
  playerKey: 'patchaga.player',
  muteKey: 'patchaga.muted',
};

/** Where column `c` sits when the formation is centred, in px. */
export const FORM_LEFT = Math.floor(
  (CONFIG.width - (CONFIG.formCols - 1) * CONFIG.colStep) / 2);

/**
 * Speeds and pressure by wave. Anything past the end repeats the last entry.
 *
 * Wave 1 is deliberately gentle: one diver at a time, slow, and firing about
 * half as often as the base rate. A first wave is where someone finds out what
 * the controls do, and a first wave nobody clears reads as a broken game rather
 * than a hard one. The ramp is steep after wave 3, and wave 4 is a regression
 * sweep, which lands as a breather exactly where it is needed.
 *
 * `diveGap` is added to CONFIG.diveGapMin, so positive is calmer. `speed` and
 * `fire` are percentages applied to the base values.
 */
export const WAVE_TIERS = [
  { diveGap: 70, speed: 88, fire: 55, divers: 1 },
  { diveGap: 40, speed: 94, fire: 70, divers: 2 },
  { diveGap: 10, speed: 100, fire: 85, divers: 2 },
  { diveGap: -10, speed: 108, fire: 100, divers: 3 },
  { diveGap: -22, speed: 116, fire: 115, divers: 4 },
  { diveGap: -32, speed: 126, fire: 130, divers: 5 },
  { diveGap: -42, speed: 138, fire: 150, divers: 6 },
];

/** The tier a wave plays at. */
export function tierFor(wave) {
  const i = Math.min(Math.max(wave - 1, 0), WAVE_TIERS.length - 1);
  return WAVE_TIERS[i];
}

/** True when this wave is a regression sweep rather than a formation wave. */
export function isSweepWave(wave) {
  return wave % CONFIG.sweepEvery === 0;
}
