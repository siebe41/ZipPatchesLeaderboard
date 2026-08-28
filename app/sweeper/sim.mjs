/**
 * The Patch Sweeper simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of input events, it produces exactly the
 * same run every time, on any machine, at any frame rate. That property is
 * the whole reason the loop is built this way, and it is what lets the
 * server replay a submitted run in Python instead of believing the score it
 * was handed.
 *
 * Positions are integers in sub-units and the heading is an integer step
 * around a table-driven circle -- see config.mjs for why. Every division
 * floors. Nothing here calls a transcendental function or a square root:
 * the ship's speed cap is per-axis rather than by magnitude for exactly
 * that reason.
 *
 * On this game's trace encoding. Every other game here packs one action
 * into `tick * 4 + action`, because steering plus an occasional fire fits in
 * two bits. A ship needs turn, thrust and fire as independent channels, so
 * this file uses three bits instead: `tick * 8 + action`. It is still one
 * flat list of integers, still read the same way, just with more room per
 * entry.
 *
 * None of the artwork or the names come from any existing arcade game. It
 * is a rotate-thrust-shoot game in open space, which is a genre, built out
 * of Patch My PC's own material: a duck-drone clearing legacy debt before
 * it collides with anything that matters.
 */
import { CONFIG, SIN_STEPS, SIN_SCALE, isin, icos, fdiv, tierSpeedPct } from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

const U = CONFIG.unit;
const PX = (px) => px * U;
const WIDTH_SU = PX(CONFIG.width);
const HEIGHT_SU = PX(CONFIG.height);

export const STATE = {
  READY: 'ready',
  PLAYING: 'playing',
  DYING: 'dying',
  CLEAR: 'clear',
  DEAD: 'dead',
};

/** Turn and thrust are held state, recorded as edges; fire is a one-shot
 * press. All six share the encoding `tick * 8 + action`. */
export const ACTION = {
  TURN_LEFT: 0, TURN_RIGHT: 1, TURN_NEUTRAL: 2,
  THRUST_ON: 3, THRUST_OFF: 4,
  FIRE: 5,
};

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

function wrap(v, max) {
  return ((v % max) + max) % max;
}

function overlaps(ax, ay, ahw, ahh, bx, by, bhw, bhh) {
  return Math.abs(ax - bx) <= PX(ahw + bhw) && Math.abs(ay - by) <= PX(ahh + bhh);
}

// --------------------------------------------------------------------------
// Building a run
// --------------------------------------------------------------------------

function makeShip() {
  return {
    x: fdiv(WIDTH_SU, 2), y: fdiv(HEIGHT_SU, 2),
    vx: 0, vy: 0, heading: 0, turnDir: 0, thrusting: false,
    cooldown: 0, iframes: CONFIG.respawnIframeTicks,
  };
}

function chunkSpeed(sim, size) {
  const range = CONFIG.chunkSpeedMaxSu[size] - CONFIG.chunkSpeedMinSu[size] + 1;
  const base = CONFIG.chunkSpeedMinSu[size] + rngInt(sim.rng, range);
  return fdiv(base * tierSpeedPct(sim.level), 100);
}

function spawnWave(sim) {
  const count = Math.min(8, CONFIG.chunkSpawnBase + (sim.level - 1));
  for (let i = 0; i < count; i++) {
    const x = rngInt(sim.rng, CONFIG.width) * U;
    const y = rngInt(sim.rng, CONFIG.height) * U;
    const heading = rngInt(sim.rng, SIN_STEPS);
    const speed = chunkSpeed(sim, 0);
    sim.chunks.push({
      x, y,
      vx: fdiv(icos(heading) * speed, SIN_SCALE),
      vy: fdiv(isin(heading) * speed, SIN_SCALE),
      size: 0,
    });
  }
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

    ship: makeShip(),
    chunks: [],
    patches: [],

    chunksDestroyed: 0,
    levelsCleared: 0,
    shotsFired: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],
    inputs: [],
    events: [],
  };
  spawnWave(sim);
  return sim;
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

export function queueInput(sim, atTick, action) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  if (t > CONFIG.absoluteMaxTicks) return;
  if (sim.inputs.length + sim.pending.length >= CONFIG.maxInputTrace) return;
  sim.pending.push(t * 8 + action);
}

function drainInput(sim) {
  let i = 0;
  while (i < sim.pending.length) {
    const code = sim.pending[i];
    if (fdiv(code, 8) > sim.tick) { i++; continue; }
    sim.pending.splice(i, 1);
    sim.inputs.push(sim.tick * 8 + (code % 8));
    applyAction(sim, code % 8);
  }
}

function fire(sim) {
  const ship = sim.ship;
  if (ship.cooldown > 0 || sim.patches.length >= CONFIG.maxPatches) return;
  ship.cooldown = CONFIG.patchCooldownTicks;
  sim.patches.push({
    x: ship.x, y: ship.y,
    vx: fdiv(icos(ship.heading) * CONFIG.patchSpeedSu, SIN_SCALE),
    vy: fdiv(isin(ship.heading) * CONFIG.patchSpeedSu, SIN_SCALE),
    life: CONFIG.patchLifetimeTicks,
  });
  sim.shotsFired += 1;
  sim.events.push({ type: 'fire' });
}

function applyAction(sim, action) {
  if (sim.state === STATE.READY) {
    if (sim.stateTick < CONFIG.readyTicks) return;
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
    sim.playStartTick = sim.tick;
  }
  if (sim.state !== STATE.PLAYING) return;
  const ship = sim.ship;
  if (action === ACTION.TURN_LEFT) ship.turnDir = -1;
  else if (action === ACTION.TURN_RIGHT) ship.turnDir = 1;
  else if (action === ACTION.TURN_NEUTRAL) ship.turnDir = 0;
  else if (action === ACTION.THRUST_ON) ship.thrusting = true;
  else if (action === ACTION.THRUST_OFF) ship.thrusting = false;
  else if (action === ACTION.FIRE) fire(sim);
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

function splitChunk(sim, chunk) {
  const nextSize = chunk.size + 1;
  if (nextSize > 2) return;
  for (let i = 0; i < 2; i++) {
    const heading = rngInt(sim.rng, SIN_STEPS);
    const speed = chunkSpeed(sim, nextSize);
    sim.chunks.push({
      x: chunk.x, y: chunk.y,
      vx: fdiv(icos(heading) * speed, SIN_SCALE),
      vy: fdiv(isin(heading) * speed, SIN_SCALE),
      size: nextSize,
    });
  }
}

function killShip(sim) {
  sim.lives -= 1;
  sim.state = STATE.DYING;
  sim.stateTick = 0;
  sim.events.push({ type: 'die' });
}

function respawnShip(sim) {
  sim.ship = makeShip();
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

function updateShip(sim) {
  const ship = sim.ship;
  ship.heading += ship.turnDir * CONFIG.turnRateSteps;
  if (ship.thrusting) {
    ship.vx = clamp(ship.vx + fdiv(icos(ship.heading) * CONFIG.thrustAccelSu, SIN_SCALE),
      -CONFIG.maxAxisSpeedSu, CONFIG.maxAxisSpeedSu);
    ship.vy = clamp(ship.vy + fdiv(isin(ship.heading) * CONFIG.thrustAccelSu, SIN_SCALE),
      -CONFIG.maxAxisSpeedSu, CONFIG.maxAxisSpeedSu);
  }
  ship.x = wrap(ship.x + ship.vx, WIDTH_SU);
  ship.y = wrap(ship.y + ship.vy, HEIGHT_SU);
  if (ship.cooldown > 0) ship.cooldown -= 1;
  if (ship.iframes > 0) ship.iframes -= 1;
}

function updatePatches(sim) {
  for (let i = sim.patches.length - 1; i >= 0; i--) {
    const p = sim.patches[i];
    p.x = wrap(p.x + p.vx, WIDTH_SU);
    p.y = wrap(p.y + p.vy, HEIGHT_SU);
    p.life -= 1;
    if (p.life <= 0) sim.patches.splice(i, 1);
  }
}

function updateChunks(sim) {
  for (const c of sim.chunks) {
    c.x = wrap(c.x + c.vx, WIDTH_SU);
    c.y = wrap(c.y + c.vy, HEIGHT_SU);
  }
}

function resolvePatchHits(sim) {
  for (let pi = sim.patches.length - 1; pi >= 0; pi--) {
    const p = sim.patches[pi];
    for (let ci = sim.chunks.length - 1; ci >= 0; ci--) {
      const c = sim.chunks[ci];
      const halfW = CONFIG.chunkHalfW[c.size];
      if (overlaps(p.x, p.y, CONFIG.patchHalfW, CONFIG.patchHalfW,
        c.x, c.y, halfW, halfW)) {
        sim.patches.splice(pi, 1);
        sim.chunks.splice(ci, 1);
        sim.chunksDestroyed += 1;
        addScore(sim, CONFIG.chunkPoints[c.size]);
        sim.events.push({ type: 'break', size: c.size });
        splitChunk(sim, c);
        break;
      }
    }
  }
}

function resolveShipHit(sim) {
  const ship = sim.ship;
  if (ship.iframes > 0) return;
  for (const c of sim.chunks) {
    const halfW = CONFIG.chunkHalfW[c.size];
    if (overlaps(ship.x, ship.y, CONFIG.shipHalfW, CONFIG.shipHalfH,
      c.x, c.y, halfW, halfW)) {
      killShip(sim);
      return;
    }
  }
}

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  drainInput(sim);

  if (sim.state === STATE.READY) {
    sim.stateTick += 1;
  } else if (sim.state === STATE.PLAYING) {
    updateShip(sim);
    updatePatches(sim);
    updateChunks(sim);
    resolvePatchHits(sim);
    if (sim.chunks.length === 0) {
      addScore(sim, CONFIG.levelClearBonus * sim.level);
      sim.levelsCleared += 1;
      sim.state = STATE.CLEAR;
      sim.stateTick = 0;
      sim.events.push({ type: 'clear' });
    } else {
      resolveShipHit(sim);
    }
  } else if (sim.state === STATE.DYING) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.dyingTicks) {
      if (sim.lives <= 0) {
        sim.state = STATE.DEAD;
        sim.endTick = sim.tick;
        sim.events.push({ type: 'gameover' });
      } else {
        respawnShip(sim);
        sim.state = STATE.PLAYING;
        sim.stateTick = 0;
      }
    }
  } else if (sim.state === STATE.CLEAR) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.clearTicks) {
      sim.level += 1;
      sim.patches = [];
      respawnShip(sim);
      spawnWave(sim);
      sim.state = STATE.PLAYING;
      sim.stateTick = 0;
    }
  }

  sim.tick += 1;
}

export function replay(seed, inputs, maxTicks) {
  const sim = createSim(seed);
  sim.pending.push(...inputs);
  const last = inputs.length ? fdiv(inputs[inputs.length - 1], 8) : 0;
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
