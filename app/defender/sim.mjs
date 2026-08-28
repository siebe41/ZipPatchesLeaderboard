/**
 * The PatchDefender simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of aim events, it produces exactly the same
 * run every time, on any machine, at any frame rate. That property is the
 * whole reason the loop is built this way, and it is what lets the server
 * replay a submitted run in Python instead of believing the score it was
 * handed.
 *
 * Positions are integers in sub-units. Every division floors. Nothing here
 * calls a square root: both a zero-day's fall and an interceptor's flight
 * are computed as "how many ticks to arrive, then move linearly over that
 * many ticks" rather than as a normalised direction times a speed, which is
 * what a square root would otherwise be needed for. A diagonal shot is
 * therefore a little faster in a straight line than an axis-aligned one of
 * the same tick budget -- a deliberate, acceptable trade for never having to
 * agree with Python about what `Math.sqrt` returns.
 *
 * None of the artwork or the names come from any existing arcade game. It
 * is a point-defence game, which is a genre, built out of Patch My PC's own
 * material: intercepting zero-days before they land on an endpoint.
 */
import { CONFIG, fdiv, tierSpeedPct, endpointX } from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

const U = CONFIG.unit;
const PX = (px) => px * U;
const WIDTH_SU = PX(CONFIG.width);
const GROUND_SU = PX(CONFIG.groundY);
const SILO_X_SU = PX(CONFIG.siloX);
const SILO_Y_SU = PX(CONFIG.siloY);

export const STATE = {
  READY: 'ready',
  PLAYING: 'playing',
  CLEAR: 'clear',
  DEAD: 'dead',
};

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

function distSq(ax, ay, bx, by) {
  const dx = ax - bx;
  const dy = ay - by;
  return dx * dx + dy * dy;
}

// --------------------------------------------------------------------------
// Building a run
// --------------------------------------------------------------------------

function spawnWave(sim) {
  const count = Math.min(CONFIG.missilesCap,
    CONFIG.missilesBase + (sim.level - 1) * CONFIG.missilesPerLevel);
  const pct = tierSpeedPct(sim.level);
  for (let i = 0; i < count; i++) {
    const startX = rngInt(sim.rng, CONFIG.width) * U;
    const targetIdx = rngInt(sim.rng, CONFIG.endpointCount);
    const targetX = PX(endpointX(targetIdx));
    const speedRange = CONFIG.missileSpeedMaxSu[0] - CONFIG.missileSpeedMinSu[0] + 1;
    const vy = fdiv((CONFIG.missileSpeedMinSu[0] + rngInt(sim.rng, speedRange)) * pct, 100);
    const ticksToGround = Math.max(1, fdiv(GROUND_SU, vy));
    const vx = fdiv(targetX - startX, ticksToGround);
    sim.missiles.push({
      x: startX, y: 0, vx, vy,
      delay: rngInt(sim.rng, CONFIG.spawnStaggerTicks),
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
    level: 1,
    nextExtraEndpoint: CONFIG.extraEndpointAt,
    endpoints: new Array(CONFIG.endpointCount).fill(true),

    missiles: [],
    interceptors: [],
    cooldown: 0,

    missilesIntercepted: 0,
    missilesLanded: 0,
    shotsFired: 0,
    levelsCleared: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],  // flat [tick, x, y, tick, x, y, ...] not yet reached
    inputs: [],   // same shape, every aim actually applied. This is the trace.
    events: [],
  };
  spawnWave(sim);
  return sim;
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

export function queueInput(sim, atTick, x, y) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  if (t > CONFIG.absoluteMaxTicks) return;
  if (sim.inputs.length / 3 + sim.pending.length / 3 >= CONFIG.maxShots) return;
  sim.pending.push(t, Math.round(x), Math.round(y));
}

function drainInput(sim) {
  let i = 0;
  while (i < sim.pending.length) {
    const t = sim.pending[i];
    if (t > sim.tick) { i += 3; continue; }
    const x = sim.pending[i + 1];
    const y = sim.pending[i + 2];
    sim.pending.splice(i, 3);
    sim.inputs.push(sim.tick, x, y);
    applyAim(sim, x, y);
  }
}

function fire(sim, targetXpx, targetYpx) {
  if (sim.cooldown > 0 || sim.interceptors.length >= CONFIG.maxInterceptors) return;
  sim.cooldown = CONFIG.fireCooldownTicks;
  const tx = PX(clamp(targetXpx, 0, CONFIG.width));
  const ty = PX(clamp(targetYpx, 0, CONFIG.groundY));
  const dx = tx - SILO_X_SU;
  const dy = ty - SILO_Y_SU;
  const spread = Math.max(Math.abs(dx), Math.abs(dy));
  const steps = Math.max(1, fdiv(spread, CONFIG.interceptorSpeedSu));
  sim.interceptors.push({
    x: SILO_X_SU, y: SILO_Y_SU,
    vx: fdiv(dx, steps), vy: fdiv(dy, steps),
    ticksLeft: steps, exploded: false, blastTicksLeft: 0,
  });
  sim.shotsFired += 1;
  sim.events.push({ type: 'fire' });
}

function applyAim(sim, x, y) {
  if (sim.state === STATE.READY) {
    if (sim.stateTick < CONFIG.readyTicks) return;
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
    sim.playStartTick = sim.tick;
  }
  if (sim.state !== STATE.PLAYING) return;
  fire(sim, x, y);
}

// --------------------------------------------------------------------------
// Scoring
// --------------------------------------------------------------------------

function addScore(sim, points) {
  sim.score = Math.min(CONFIG.maxScore, sim.score + points);
  if (sim.score < sim.nextExtraEndpoint) return;
  const deadIdx = sim.endpoints.findIndex((alive) => !alive);
  if (deadIdx < 0) return;
  sim.endpoints[deadIdx] = true;
  sim.nextExtraEndpoint += CONFIG.extraEndpointEvery;
  sim.events.push({ type: 'extralife' });
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

function updateMissiles(sim) {
  for (let i = sim.missiles.length - 1; i >= 0; i--) {
    const m = sim.missiles[i];
    if (m.delay > 0) { m.delay -= 1; continue; }
    m.x += m.vx;
    m.y += m.vy;
    if (m.y < GROUND_SU) continue;

    sim.missiles.splice(i, 1);
    sim.missilesLanded += 1;
    const hitRadius = PX(CONFIG.endpointHalfW);
    for (let e = 0; e < CONFIG.endpointCount; e++) {
      if (!sim.endpoints[e]) continue;
      if (Math.abs(m.x - PX(endpointX(e))) <= hitRadius) {
        sim.endpoints[e] = false;
        sim.events.push({ type: 'endpointlost' });
        break;
      }
    }
    if (sim.endpoints.every((alive) => !alive)) {
      sim.state = STATE.DEAD;
      sim.endTick = sim.tick;
      sim.events.push({ type: 'gameover' });
      return;
    }
  }
}

function updateInterceptors(sim) {
  const blastRadiusSu = PX(CONFIG.blastRadiusPx);
  const blastSq = blastRadiusSu * blastRadiusSu;
  for (let i = sim.interceptors.length - 1; i >= 0; i--) {
    const p = sim.interceptors[i];
    if (!p.exploded) {
      p.x += p.vx;
      p.y += p.vy;
      p.ticksLeft -= 1;
      if (p.ticksLeft <= 0) {
        p.exploded = true;
        p.blastTicksLeft = CONFIG.blastTicks;
        sim.events.push({ type: 'explode' });
      }
      continue;
    }
    for (let mi = sim.missiles.length - 1; mi >= 0; mi--) {
      const m = sim.missiles[mi];
      if (m.delay > 0) continue;
      if (distSq(p.x, p.y, m.x, m.y) <= blastSq) {
        sim.missiles.splice(mi, 1);
        sim.missilesIntercepted += 1;
        addScore(sim, CONFIG.missilePoints);
        sim.events.push({ type: 'kill' });
      }
    }
    p.blastTicksLeft -= 1;
    if (p.blastTicksLeft <= 0) sim.interceptors.splice(i, 1);
  }
}

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  drainInput(sim);

  if (sim.state === STATE.READY) {
    sim.stateTick += 1;
  } else if (sim.state === STATE.PLAYING) {
    if (sim.cooldown > 0) sim.cooldown -= 1;
    updateMissiles(sim);
    if (sim.state === STATE.DEAD) { sim.tick += 1; return; }
    updateInterceptors(sim);
    if (sim.missiles.length === 0) {
      let bonus = 0;
      for (const alive of sim.endpoints) if (alive) bonus += CONFIG.endpointBonus;
      addScore(sim, bonus);
      sim.levelsCleared += 1;
      sim.interceptors = [];
      sim.state = STATE.CLEAR;
      sim.stateTick = 0;
      sim.events.push({ type: 'clear' });
    }
  } else if (sim.state === STATE.CLEAR) {
    sim.stateTick += 1;
    if (sim.stateTick >= CONFIG.clearTicks) {
      sim.level += 1;
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
  let last = 0;
  for (let i = 0; i < inputs.length; i += 3) last = inputs[i];
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
