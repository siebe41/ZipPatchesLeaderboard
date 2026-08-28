/**
 * Every tuning constant for Ducker, in one place.
 *
 * A crossing game lives or dies on its numbers, so if a value that affects
 * how the game feels lives anywhere else in this folder, it is in the wrong
 * place.
 *
 * On units. The simulation never uses pixels directly for anything that
 * moves in less than a pixel per tick. Positions and speeds that need that
 * precision hold x as an integer in *sub-units*, and one pixel is `unit` of
 * them. That is not fussiness: the server replays every submitted run in
 * Python to decide the score, and integer arithmetic is the only kind two
 * languages agree on without argument. The duck's row, by contrast, never
 * needs fractional precision -- lanes are the only things that drift -- so it
 * is kept as a plain grid index.
 *
 * On division. Both engines floor. JavaScript writes `Math.floor(a / b)` and
 * Python writes `a // b`, and those agree for negative operands too, which
 * `Math.trunc` and C-style truncation would not. Every division in the
 * simulation is a floor division for that reason.
 */

export const CONFIG = {
  // --- Canvas and grid -----------------------------------------------------
  cell: 40,              // px, both lane height and column width
  cols: 13,               // 0..12; odd, so there is a true centre column
  unit: 64,               // sub-units per pixel

  // Row 0 is the goal row at the top of the screen; row 12 is the start row
  // at the bottom. Everything in between is a lane.
  rows: 13,
  goalRow: 0,
  riverTop: 1,
  riverBottom: 5,
  medianRow: 6,
  roadTop: 7,
  roadBottom: 11,
  startRow: 12,
  startCol: 6,
  hudTop: 40,             // px of score strip above the playfield

  // --- Simulation ------------------------------------------------------------
  stepMs: 1000 / 120,
  maxCatchUpSteps: 480,   // a backgrounded tab hands back minutes; do not chase it

  // --- The duck --------------------------------------------------------------
  duckHalfW: 14,          // px, collision only. Smaller than the drawn duck:
  duckHalfH: 14,           // a near miss should read as a near miss.
  hopTicks: 7,             // ticks one hop takes, and the lockout between hops

  // --- The goal row ------------------------------------------------------------
  // Five slots, evenly spaced across 13 columns, mirrors the genre's home
  // row. Landing outside a slot's span is the hedge: it costs a life just
  // like traffic does.
  //
  // A hop always lands on a grid column (see snapCol() in sim.mjs), and with
  // slots three columns apart, every column is at most one cell from its
  // nearest slot. A half-width under one cell means only the slot's own
  // column ever counts, so anyone who drifted a single column off during the
  // crossing dies at the hedge despite having been visibly lined up with a
  // slot -- a difficulty that reads as unfair rather than hard. One full
  // cell of half-width is what makes every column reach its nearest slot.
  slotCols: [0, 3, 6, 9, 12],
  slotHalfW: 40,           // px either side of a slot's centre that still counts

  // --- Lanes -------------------------------------------------------------------
  // Index 0 is nearest the safe rows on each side, working outward toward the
  // median. Direction alternates by construction (see laneDir below) so
  // neighbouring lanes never move the same way, which is what makes reading
  // the road at a glance possible.
  //
  // Speeds are sub-units/tick at level 1, before the level's speed tier is
  // applied. Held as plain integers, never derived from a pixel-per-second
  // figure at runtime: scaling one by a percent and rounding is exactly the
  // kind of arithmetic a browser and a Python port are not guaranteed to
  // round the same way at a .5 boundary, so the numbers below are the
  // tuning, not a rounding of some other number.
  roadSpeedSu: [24, 32, 40, 50, 60],
  riverSpeedSu: [22, 30, 38, 46, 54],
  roadEntityHalfW: 16,     // px; the beetle

  // The raft is wider and there are more of them per lane than the beetle
  // count would suggest, and deliberately so: a beetle missed just costs a
  // wait for the next gap, but a raft missed is a life gone with no recourse,
  // so the river has to be the more forgiving of the two to land at the same
  // felt difficulty. At the old width and count, a raft covered roughly 30%
  // of the lane at any instant -- most arrivals were a blind gamble no matter
  // how well the road crossing went. This tiling covers roughly 55-60%.
  riverEntityHalfW: 50,    // px; the patch-note raft
  roadEntitiesPerLane: 3,
  riverEntitiesPerLane: 4,
  laneWrapMargin: 2,       // extra cells of travel before an entity wraps

  // --- Timing ------------------------------------------------------------------
  readyTicks: 60,           // ticks of "GET READY" before the first hop
  dyingTicks: 60,           // ticks the duck spends reacting to a death
  clearTicks: 150,          // ticks of celebration once every slot is filled
  // 40s at 120Hz. Careful, well-timed crossing of ten lanes plus a goal
  // placement does not fit in 30s without rushing hops past traffic that
  // has not actually opened a gap yet -- the timer was making the run
  // punishing on its own, independent of how forgiving any single lane is.
  lifeTicks: 4800,

  // --- Scoring -------------------------------------------------------------------
  rowPoints: 10,            // once per row, the first time it is reached this life
  slotPoints: 50,
  timeBonusDivisor: 10,     // remaining life-ticks / this, added on a slot
  levelClearBonus: 1000,    // times the level, once every slot is filled
  extraLifeAt: 20000,
  extraLifeEvery: 60000,
  maxLives: 6,
  lives: 4,

  // --- Level scaling ---------------------------------------------------------
  // Percent applied to the base lane speeds above. Capped at the last tier
  // rather than climbing forever, because a lane that outruns what a hop can
  // dodge stops being a game and starts being a coin flip.
  levelSpeedPct: [100, 112, 125, 138, 150, 162, 175],

  // --- Limits both engines share ---------------------------------------------
  // A trace longer than this is refused rather than replayed. Frogger-style
  // hopping is far less frequent than firing a gun, so the ceiling is smaller
  // than the shooter's: a hop every ten ticks for the full twelve minutes is
  // 8,640 presses, and 12,000 leaves comfortable room above that.
  maxInputTrace: 12000,
  absoluteMaxTicks: 120 * 60 * 12,  // twelve minutes
  tailTicks: 120 * 30,               // room for a timed-out life to resolve
  maxScore: 10000000,

  // --- Presentation only -------------------------------------------------------
  bestKey: 'ducker.best',
  playerKey: 'ducker.player',
  muteKey: 'ducker.muted',
};

/** Floor division, named so the Python port has an obvious counterpart. */
export function fdiv(a, b) {
  return Math.floor(a / b);
}

/** True for a lane index that moves right-to-left, at level 1 and always. */
export function laneDir(index) {
  return index % 2 === 0 ? 1 : -1;
}

/** The road lane rows, top (nearest the median) to bottom (nearest home). */
export const ROAD_ROWS = [11, 10, 9, 8, 7];

/** The river lane rows, top (nearest the goal) to bottom (nearest the median). */
export const RIVER_ROWS = [5, 4, 3, 2, 1];

/** The speed percent a level plays at. */
export function tierSpeedPct(level) {
  const i = Math.min(Math.max(level - 1, 0), CONFIG.levelSpeedPct.length - 1);
  return CONFIG.levelSpeedPct[i];
}
