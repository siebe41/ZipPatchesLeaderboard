/**
 * The Patch Trail simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of input events, it produces exactly the
 * same run every time, on any machine, at any frame rate. That property is
 * the whole reason the loop is built this way, and it is what lets the
 * server replay a submitted run in Python instead of believing the score it
 * was handed.
 *
 * Positions are plain column/row integers -- nothing here moves by less
 * than a whole cell, so there is no sub-unit scale to keep in step. The one
 * place the two engines have to agree exactly on more than arithmetic is
 * where the next patch spawns, which is why spawnPatch()'s retry-then-scan
 * shape is ported line for line rather than approximated.
 *
 * None of the artwork or the names come from any existing arcade game. It
 * is a growing-trail game, which is a genre, built out of Patch My PC's own
 * material: a duck towing a lengthening chain of deployed patches.
 */
import { CONFIG, fdiv, moveTicks } from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

export const STATE = {
  READY: 'ready',
  PLAYING: 'playing',
  DYING: 'dying',
  CLEAR: 'clear',
  DEAD: 'dead',
};

/**
 * The four things a player can do. These are *edges*: a turn is recorded
 * once, at the tick the key went down, matching the `tick * 4 + action`
 * encoding every other game here uses.
 */
export const ACTION = { UP: 0, DOWN: 1, LEFT: 2, RIGHT: 3 };

const DX = [0, 0, -1, 1];
const DY = [-1, 1, 0, 0];
const OPPOSITE = [1, 0, 3, 2];

function occupied(segments, col, row, limit) {
  const n = limit == null ? segments.length : limit;
  for (let i = 0; i < n; i++) {
    if (segments[i].col === col && segments[i].row === row) return true;
  }
  return false;
}

function startSegments() {
  const headCol = fdiv(CONFIG.cols, 2);
  const headRow = fdiv(CONFIG.rows, 2);
  const segments = [];
  for (let i = 0; i < CONFIG.startLength; i++) {
    segments.push({ col: headCol - i, row: headRow });
  }
  return segments;
}

/**
 * Pick a cell for the next patch. Tries a bounded number of random draws
 * first -- cheap, and almost always enough room on this grid -- and falls
 * back to an exhaustive scan of every empty cell if the board is crowded.
 * Both branches are ported to Python exactly as written: which one runs,
 * and how many draws it costs the generator, has to match between the two
 * engines or the RNG stream after this point desyncs.
 */
function spawnPatch(sim) {
  for (let i = 0; i < CONFIG.patchSpawnRetries; i++) {
    const col = rngInt(sim.rng, CONFIG.cols);
    const row = rngInt(sim.rng, CONFIG.rows);
    if (!occupied(sim.segments, col, row)) {
      sim.patch = { col, row };
      return;
    }
  }
  const empties = [];
  for (let r = 0; r < CONFIG.rows; r++) {
    for (let c = 0; c < CONFIG.cols; c++) {
      if (!occupied(sim.segments, c, r)) empties.push({ col: c, row: r });
    }
  }
  if (empties.length === 0) {
    sim.patch = { col: -1, row: -1 };
    return;
  }
  sim.patch = empties[rngInt(sim.rng, empties.length)];
}

export function createSim(seed) {
  const sim = {
    seed: seed >>> 0,
    rng: makeRng(seed),
    tick: 0,
    state: STATE.READY,
    stateTick: 0,

    score: 0,
    lives: CONFIG.lives,
    level: 1,
    nextExtraLife: CONFIG.extraLifeAt,

    segments: startSegments(),
    dir: ACTION.RIGHT,
    pendingDir: ACTION.RIGHT,
    moveTimer: moveTicks(1),
    patch: { col: -1, row: -1 },

    patchesThisLevel: 0,
    patchesEaten: 0,
    levelsCleared: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],
    inputs: [],
    events: [],
  };
  spawnPatch(sim);
  return sim;
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

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

function applyAction(sim, action) {
  if (sim.state === STATE.READY) {
    if (sim.stateTick < CONFIG.readyTicks) return;
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
    sim.playStartTick = sim.tick;
  }
  if (sim.state !== STATE.PLAYING) return;
  // A single grid step never reverses into itself, so a direction opposite
  // the trail's *current* heading is refused outright -- not banked for
  // later, which would let two rapid presses turn a live U-turn into one.
  if (action === OPPOSITE[sim.dir]) return;
  sim.pendingDir = action;
}

// --------------------------------------------------------------------------
// Scoring and life cycle
// --------------------------------------------------------------------------

function addScore(sim, points) {
  sim.score = Math.min(CONFIG.maxScore, sim.score + points);
  if (sim.score >= sim.nextExtraLife && sim.lives < CONFIG.maxLives) {
    sim.lives += 1;
    sim.nextExtraLife += CONFIG.extraLifeEvery;
    sim.events.push({ type: 'extralife' });
  }
}

function killTrail(sim, reason) {
  sim.lives -= 1;
  sim.state = STATE.DYING;
  sim.stateTick = 0;
  sim.events.push({ type: reason });
}

function respawn(sim) {
  sim.segments = startSegments();
  sim.dir = ACTION.RIGHT;
  sim.pendingDir = ACTION.RIGHT;
  sim.moveTimer = moveTicks(sim.level);
  spawnPatch(sim);
}

function stepMove(sim) {
  sim.dir = sim.pendingDir;
  const head = sim.segments[0];
  const newHead = { col: head.col + DX[sim.dir], row: head.row + DY[sim.dir] };

  if (newHead.col < 0 || newHead.col >= CONFIG.cols
      || newHead.row < 0 || newHead.row >= CONFIG.rows) {
    killTrail(sim, 'wall');
    return;
  }

  const ate = newHead.col === sim.patch.col && newHead.row === sim.patch.row;
  // The tail is about to vacate its cell this step, unless the trail is
  // growing -- so a step onto the current tail is only safe when nothing is
  // eaten there.
  const bodyLimit = ate ? sim.segments.length : sim.segments.length - 1;
  if (occupied(sim.segments, newHead.col, newHead.row, bodyLimit)) {
    killTrail(sim, 'tangled');
    return;
  }

  sim.segments.unshift(newHead);
  if (ate) {
    sim.patchesThisLevel += 1;
    sim.patchesEaten += 1;
    addScore(sim, CONFIG.pointsPerPatch);
    sim.events.push({ type: 'eat' });
    spawnPatch(sim);
    if (sim.patchesThisLevel >= CONFIG.patchesToLevelUp) {
      addScore(sim, CONFIG.levelClearBonus * sim.level);
      sim.levelsCleared += 1;
      sim.state = STATE.CLEAR;
      sim.stateTick = 0;
      sim.events.push({ type: 'clear' });
    }
  } else {
    sim.segments.pop();
  }
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  drainInput(sim);

  if (sim.state === STATE.READY) {
    sim.stateTick += 1;
  } else if (sim.state === STATE.PLAYING) {
    if (sim.moveTimer > 0) {
      sim.moveTimer -= 1;
    } else {
      stepMove(sim);
      if (sim.state === STATE.PLAYING) sim.moveTimer = moveTicks(sim.level);
    }
  } else if (sim.state === STATE.DYING) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.dyingTicks) {
      if (sim.lives <= 0) {
        sim.state = STATE.DEAD;
        sim.endTick = sim.tick;
        sim.events.push({ type: 'gameover' });
      } else {
        respawn(sim);
        sim.state = STATE.PLAYING;
        sim.stateTick = 0;
      }
    }
  } else if (sim.state === STATE.CLEAR) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.clearTicks) {
      sim.level += 1;
      sim.patchesThisLevel = 0;
      sim.moveTimer = moveTicks(sim.level);
      sim.state = STATE.PLAYING;
      sim.stateTick = 0;
    }
  }

  sim.tick += 1;
}

export function replay(seed, inputs, maxTicks) {
  const sim = createSim(seed);
  sim.pending.push(...inputs);
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
