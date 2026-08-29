/**
 * The Ducker simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of input events, it produces exactly the
 * same run every time, on any machine, at any frame rate. That property is
 * the whole reason the loop is built this way, and it is what lets the
 * server replay a submitted run in Python instead of believing the score it
 * was handed.
 *
 * The caller advances the world by calling step() exactly once per fixed
 * timestep. Rendering interpolates between the previous and current position
 * of anything that moves and never writes back into these numbers.
 *
 * The duck's row is a plain grid index -- lanes are the only things that
 * drift, so a row never needs fractional precision. Everything that can move
 * by less than a pixel in a tick (lane traffic, and the duck while it rides
 * one) is held as an integer in sub-units instead. Every division floors.
 * Those rules are what keep this file and ducker.py agreeing tick for tick.
 *
 * None of the artwork or the names come from any existing arcade game. It is
 * a lane-crossing game, which is a genre, built out of Patch My PC's own
 * material: a rubber duck crossing a highway of vulnerabilities to reach the
 * patch notes on the other side.
 */
import { CONFIG, ROAD_ROWS, RIVER_ROWS, laneDir, tierSpeedPct, fdiv } from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

const U = CONFIG.unit;
const PX = (px) => px * U;
const CELL_SU = CONFIG.cell * U;
const WIDTH_SU = CONFIG.cols * CELL_SU;
const MARGIN_SU = 2 * CELL_SU;
const WRAP_SU = WIDTH_SU + 2 * MARGIN_SU;

export const STATE = {
  READY: 'ready',     // "GET READY", nothing moving yet
  PLAYING: 'playing',
  DYING: 'dying',      // reacting to a death, before a respawn or game over
  CLEAR: 'clear',      // every slot filled, celebrating before the next level
  DEAD: 'dead',        // out of lives, the run is over
};

/**
 * The four things a player can do.
 *
 * These are *edges*, not held state: a hop is recorded at the tick the key
 * went down, once, however long it is held. The encoding is `tick * 4 +
 * action`, matching the other games here, so the server's trace reader and
 * its interval statistics work on all of them without a second
 * implementation.
 */
export const ACTION = { UP: 0, DOWN: 1, LEFT: 2, RIGHT: 3 };

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

/** The nearest column's x, in sub-units. Floor-based, so it never needs a
 * rounding mode a browser and a Python port might not agree on. */
function snapCol(x) {
  const col = fdiv(x + fdiv(CELL_SU, 2), CELL_SU);
  return clamp(col, 0, CONFIG.cols - 1) * CELL_SU;
}

// --------------------------------------------------------------------------
// Lanes
// --------------------------------------------------------------------------
//
// A lane is a conveyor: `entitiesPerLane` evenly spaced entities travel in
// one direction and wrap back onto the far edge when they clear the screen
// plus a margin. The spacing between them is fixed by the lane; the only
// thing the seed decides is where in that spacing the first entity starts,
// which is what keeps five lanes of identical entities from ever looking
// synchronised.

function makeLane(row, index, dir, speedSu, halfW, count, rng) {
  const gap = fdiv(WRAP_SU, count);
  const phase = rngInt(rng, gap);
  const entities = [];
  for (let i = 0; i < count; i++) {
    const loop = (i * gap + phase) % WRAP_SU;
    entities.push({ x: loop - MARGIN_SU, halfW });
  }
  return { row, index, dir, speedSu, halfW, entities };
}

function buildLanes(sim, rng) {
  const pct = tierSpeedPct(sim.level);
  sim.roadLanes = ROAD_ROWS.map((row, i) => makeLane(
    row, i, laneDir(i), fdiv(CONFIG.roadSpeedSu[i] * pct, 100),
    CONFIG.roadEntityHalfW, CONFIG.roadEntitiesPerLane, rng,
  ));
  sim.riverLanes = RIVER_ROWS.map((row, i) => makeLane(
    row, i, laneDir(i + 1), fdiv(CONFIG.riverSpeedSu[i] * pct, 100),
    CONFIG.riverEntityHalfW, CONFIG.riverEntitiesPerLane, rng,
  ));
}

function laneForRow(sim, row) {
  const ri = ROAD_ROWS.indexOf(row);
  if (ri >= 0) return { lane: sim.roadLanes[ri], kind: 'road' };
  const vi = RIVER_ROWS.indexOf(row);
  if (vi >= 0) return { lane: sim.riverLanes[vi], kind: 'river' };
  return null;
}

function advanceLane(lane) {
  for (const e of lane.entities) {
    e.x += lane.dir * lane.speedSu;
    if (lane.dir > 0 && e.x - PX(e.halfW) > WIDTH_SU + MARGIN_SU) e.x -= WRAP_SU;
    else if (lane.dir < 0 && e.x + PX(e.halfW) < -MARGIN_SU) e.x += WRAP_SU;
  }
}

function advanceLanes(sim) {
  for (const lane of sim.roadLanes) advanceLane(lane);
  for (const lane of sim.riverLanes) advanceLane(lane);
}

// --------------------------------------------------------------------------
// Building a run
// --------------------------------------------------------------------------

function makeDuck() {
  return {
    x: CONFIG.startCol * CELL_SU,
    row: CONFIG.startRow,
    alive: true,
  };
}

export function createSim(seed) {
  const rng = makeRng(seed);
  const sim = {
    seed: seed >>> 0,
    rng,
    tick: 0,
    state: STATE.READY,
    stateTick: 0,

    score: 0,
    lives: CONFIG.lives,
    level: 1,
    nextExtraLife: CONFIG.extraLifeAt,

    duck: makeDuck(),
    prevDuckX: CONFIG.startCol * CELL_SU,
    prevDuckRow: CONFIG.startRow,
    deepestRow: CONFIG.startRow,
    lifeTicksLeft: CONFIG.lifeTicks,
    slotsFilled: CONFIG.slotCols.map(() => false),
    hopLockUntil: 0,

    // Totals worth keeping for the board and the player page.
    slotsCleared: 0,
    levelsCleared: 0,
    hops: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],  // inputs queued but not yet reached
    inputs: [],   // every input actually applied. This is the trace.
    events: [],   // drained by the presentation layer; never read back here
  };
  buildLanes(sim, rng);
  return sim;
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

/**
 * Ask for an input at a specific simulation tick.
 *
 * Input is quantised to the timestep rather than to the frame, which is what
 * makes a run identical at 30, 60 and 144 Hz. A tick that has already been
 * simulated cannot be revisited, so a late request lands on the next one.
 */
export function queueInput(sim, atTick, action) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  if (t > CONFIG.absoluteMaxTicks) return;
  if (sim.inputs.length + sim.pending.length >= CONFIG.maxInputTrace) return;
  sim.pending.push(t * 4 + action);
}

function drainInput(sim) {
  let i = 0;
  while (i < sim.pending.length) {
    const code = sim.pending[i];
    if (fdiv(code, 4) > sim.tick) { i++; continue; }
    sim.pending.splice(i, 1);
    sim.inputs.push(sim.tick * 4 + (code % 4));
    applyAction(sim, code % 4);
  }
}

function addScore(sim, points) {
  sim.score = Math.min(CONFIG.maxScore, sim.score + points);
  if (sim.score >= sim.nextExtraLife && sim.lives < CONFIG.maxLives) {
    sim.lives += 1;
    sim.nextExtraLife += CONFIG.extraLifeEvery;
    sim.events.push({ type: 'extralife' });
  }
}

function respawnDuck(sim) {
  sim.duck.x = CONFIG.startCol * CELL_SU;
  sim.duck.row = CONFIG.startRow;
  sim.deepestRow = CONFIG.startRow;
  sim.lifeTicksLeft = CONFIG.lifeTicks;
  sim.hopLockUntil = sim.tick + CONFIG.hopTicks;
}

function killDuck(sim, reason) {
  sim.lives -= 1;
  sim.state = STATE.DYING;
  sim.stateTick = 0;
  sim.events.push({ type: reason });
}

/**
 * The player has hopped from the top river lane toward the goal row.
 *
 * Picks the nearest slot in range that is still open, not just the nearest
 * slot -- a full house at the closest marker should not sink a run that
 * could just as easily have landed at the one next door.
 */
function resolveGoalAttempt(sim) {
  const duck = sim.duck;
  let slot = -1;
  let best = Infinity;
  for (let i = 0; i < CONFIG.slotCols.length; i++) {
    if (sim.slotsFilled[i]) continue;
    const dist = Math.abs(duck.x - CONFIG.slotCols[i] * CELL_SU);
    if (dist <= PX(CONFIG.slotHalfW) && dist < best) {
      best = dist;
      slot = i;
    }
  }
  if (slot < 0) {
    killDuck(sim, 'hedge');
    return;
  }
  sim.slotsFilled[slot] = true;
  sim.slotsCleared += 1;
  duck.x = CONFIG.slotCols[slot] * CELL_SU;
  duck.row = CONFIG.goalRow;
  const bonus = fdiv(sim.lifeTicksLeft, CONFIG.timeBonusDivisor);
  addScore(sim, CONFIG.slotPoints + bonus);
  sim.events.push({ type: 'slot' });

  if (sim.slotsFilled.every(Boolean)) {
    addScore(sim, CONFIG.levelClearBonus * sim.level);
    sim.levelsCleared += 1;
    sim.state = STATE.CLEAR;
    sim.stateTick = 0;
    sim.events.push({ type: 'clear' });
  } else {
    respawnDuck(sim);
  }
}

function applyAction(sim, action) {
  // The first input is what starts the run, so a player who never touches
  // the controls never starts a clock the server will later measure them
  // against.
  if (sim.state === STATE.READY) {
    if (sim.stateTick < CONFIG.readyTicks) return;
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
    sim.playStartTick = sim.tick;
  }
  if (sim.state !== STATE.PLAYING) return;
  if (sim.tick < sim.hopLockUntil) return;

  sim.hopLockUntil = sim.tick + CONFIG.hopTicks;
  sim.hops += 1;
  const duck = sim.duck;

  // A raft carries the duck by a fraction of a cell every tick, so time spent
  // riding one leaves the duck's column off-grid by however much it drifted.
  // Left uncorrected, that drift is permanent -- a later LEFT or RIGHT moves
  // by exactly one cell either way, so it can shift a misaligned duck to
  // another misaligned position but never back onto one. A hop is a decision
  // to be *somewhere*, so it snaps to the nearest column first and moves from
  // there: drift only ever costs the ticks spent not hopping, never the hop
  // itself.
  duck.x = snapCol(duck.x);

  if (action === ACTION.UP && duck.row === CONFIG.riverTop) {
    resolveGoalAttempt(sim);
    return;
  }
  if (action === ACTION.UP) {
    duck.row = clamp(duck.row - 1, CONFIG.riverTop, CONFIG.startRow);
  } else if (action === ACTION.DOWN) {
    duck.row = clamp(duck.row + 1, CONFIG.riverTop, CONFIG.startRow);
  } else if (action === ACTION.LEFT) {
    duck.x = clamp(duck.x - CELL_SU, 0, (CONFIG.cols - 1) * CELL_SU);
  } else if (action === ACTION.RIGHT) {
    duck.x = clamp(duck.x + CELL_SU, 0, (CONFIG.cols - 1) * CELL_SU);
  }

  if (duck.row < sim.deepestRow) {
    sim.deepestRow = duck.row;
    addScore(sim, CONFIG.rowPoints);
  }
  sim.events.push({ type: 'hop' });
}

// --------------------------------------------------------------------------
// Hazards
// --------------------------------------------------------------------------

function checkHazards(sim) {
  const duck = sim.duck;
  const found = laneForRow(sim, duck.row);
  if (!found) return; // median or start row: always safe

  if (found.kind === 'road') {
    for (const e of found.lane.entities) {
      if (Math.abs(duck.x - e.x) <= PX(CONFIG.duckHalfW) + PX(e.halfW)) {
        killDuck(sim, 'squish');
        return;
      }
    }
    return;
  }

  // River: the duck only survives a tick here riding something. Its centre
  // has to fall within the raft's span, not merely overlap it -- a duck
  // hanging half off the edge of a log is exactly the near miss the genre is
  // built on, and it belongs to the water, not the raft.
  let riding = null;
  for (const e of found.lane.entities) {
    if (Math.abs(duck.x - e.x) <= PX(e.halfW)) { riding = e; break; }
  }
  if (!riding) {
    killDuck(sim, 'drowned');
    return;
  }
  duck.x += found.lane.dir * found.lane.speedSu;
  if (duck.x < 0 || duck.x > (CONFIG.cols - 1) * CELL_SU) {
    killDuck(sim, 'drowned');
  }
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  sim.prevDuckX = sim.duck.x;
  sim.prevDuckRow = sim.duck.row;

  drainInput(sim);
  advanceLanes(sim);

  if (sim.state === STATE.READY) {
    sim.stateTick += 1;
  } else if (sim.state === STATE.PLAYING) {
    checkHazards(sim);
    if (sim.state === STATE.PLAYING) {
      sim.lifeTicksLeft -= 1;
      if (sim.lifeTicksLeft <= 0) killDuck(sim, 'timeout');
    }
  } else if (sim.state === STATE.DYING) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.dyingTicks) {
      if (sim.lives <= 0) {
        sim.state = STATE.DEAD;
        sim.endTick = sim.tick;
        sim.events.push({ type: 'gameover' });
      } else {
        respawnDuck(sim);
        sim.state = STATE.PLAYING;
        sim.stateTick = 0;
      }
    }
  } else if (sim.state === STATE.CLEAR) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.clearTicks) {
      sim.level += 1;
      sim.slotsFilled = CONFIG.slotCols.map(() => false);
      buildLanes(sim, sim.rng);
      respawnDuck(sim);
      sim.state = STATE.PLAYING;
      sim.stateTick = 0;
    }
  }

  sim.tick += 1;
}

export function replay(seed, inputs, maxTicks) {
  const sim = createSim(seed);
  for (const code of inputs) {
    sim.pending.push(code); // already `tick * 4 + action`; queueInput would only re-clamp
  }
  const last = inputs.length ? fdiv(inputs[inputs.length - 1], 4) : 0;
  const ceiling = maxTicks != null ? maxTicks
    : Math.min(last + CONFIG.tailTicks, CONFIG.absoluteMaxTicks);

  while (sim.state !== STATE.DEAD && sim.tick < ceiling) step(sim);
  return sim;
}

export function durationMs(sim) {
  if (sim.playStartTick < 0) return 0;
  const end = sim.endTick >= 0 ? sim.endTick : sim.tick;
  return Math.round((end - sim.playStartTick) * CONFIG.stepMs);
}
