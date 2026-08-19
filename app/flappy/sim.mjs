/**
 * The Flappy Duck simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random. Given a
 * seed and the list of ticks a flap happened on, it produces exactly the same
 * run every time, on any machine, at any frame rate. That property is the
 * whole reason the loop is built this way.
 *
 * The caller advances the world by calling step() exactly once per fixed
 * timestep. Rendering interpolates between the previous and current state and
 * never touches these numbers.
 */
import { CONFIG, SIM_DT } from './config.mjs';
import { makeRng, hash32 } from './rng.mjs';

export const STATE = {
  READY: 'ready',
  PLAYING: 'playing',
  DYING: 'dying',
  DEAD: 'dead',
};

export const CAUSE = {
  OBSTACLE: 'obstacle',
  GROUND: 'ground',
};

export function createSim(seed) {
  return {
    seed: seed >>> 0,
    rng: makeRng(seed),
    gaps: [], // gap centre per obstacle index, generated lazily in order
    tick: 0,
    state: STATE.READY,
    duckY: CONFIG.startY,
    duckVy: 0,
    prevDuckY: CONFIG.startY,
    prevScrollX: 0,
    scrollTicks: 0, // only advances while playing, so death freezes the world
    scrollX: 0,
    score: 0,
    nextScoreIndex: 0,
    playStartTick: -1,
    deathTick: -1,
    cause: null,
    pending: [], // flap ticks queued but not yet reached
    flaps: [], // every flap actually applied, by tick. This is the trace.
    events: [], // drained by the presentation layer for audio and effects
  };
}

/**
 * Ask for a flap at a specific simulation tick.
 *
 * Input is quantised to the timestep rather than to the frame, which is what
 * makes a run identical at 30, 60 and 144 Hz. A tick that has already been
 * simulated cannot be revisited, so a late request lands on the next one.
 */
export function queueFlap(sim, atTick) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  sim.pending.push(t);
  sim.pending.sort((a, b) => a - b);
}

/** The gap centre for an obstacle, generated in index order and memoised. */
function gapCenter(sim, index) {
  while (sim.gaps.length <= index) {
    let lo = CONFIG.gapCenterMin;
    let hi = CONFIG.gapCenterMax;
    if (sim.gaps.length > 0) {
      const prev = sim.gaps[sim.gaps.length - 1];
      lo = Math.max(lo, prev - CONFIG.gapCenterMaxDelta);
      hi = Math.min(hi, prev + CONFIG.gapCenterMaxDelta);
    }
    // Integers keep the sim exactly reproducible in another language.
    sim.gaps.push(lo + Math.floor(sim.rng() * (hi - lo + 1)));
  }
  return sim.gaps[index];
}

export function obstacleScreenX(sim, index) {
  return CONFIG.firstObstacleX + index * CONFIG.spacing - sim.scrollX;
}

/** Indices of the obstacles that could be drawn or hit this tick. */
export function visibleRange(sim, scrollX = sim.scrollX) {
  const base = CONFIG.firstObstacleX - scrollX;
  const first = Math.max(0, Math.ceil((-CONFIG.capW - base) / CONFIG.spacing));
  const last = Math.floor((CONFIG.width + CONFIG.capW - base) / CONFIG.spacing);
  return { first, last };
}

export function obstacleAt(sim, index, scrollX = sim.scrollX) {
  const center = gapCenter(sim, index);
  return {
    index,
    x: CONFIG.firstObstacleX + index * CONFIG.spacing - scrollX,
    gapTop: center - CONFIG.gapHeight / 2,
    gapBottom: center + CONFIG.gapHeight / 2,
    center,
    version: versionLabel(sim.seed, index),
  };
}

/** Cosmetic only. Never consumes the simulation's RNG stream. */
export function versionLabel(seed, index) {
  const h = hash32(seed, index);
  const major = 1 + (h % 9);
  const minor = (h >>> 4) % 10;
  const patch = (h >>> 9) % 40;
  return 'V' + major + '.' + minor + '.' + patch;
}

export function duckHitbox(duckY) {
  return {
    x: CONFIG.duckX + (CONFIG.duckW - CONFIG.hitW) / 2,
    y: duckY + (CONFIG.duckH - CONFIG.hitH) / 2,
    w: CONFIG.hitW,
    h: CONFIG.hitH,
  };
}

function overlaps(box, x, y, w, h) {
  return box.x < x + w && box.x + box.w > x && box.y < y + h && box.y + box.h > y;
}

function applyFlap(sim) {
  // Replace the velocity, never add to it.
  sim.duckVy = CONFIG.flapImpulse;
  sim.flaps.push(sim.tick);
  sim.events.push({ type: 'flap', tick: sim.tick });
}

function die(sim, cause) {
  sim.state = STATE.DYING;
  sim.deathTick = sim.tick;
  sim.cause = cause;
  sim.events.push({ type: 'crash', tick: sim.tick, cause });
  if (cause === CAUSE.OBSTACLE) {
    // A small hop off the tile, so the fall reads as a consequence rather
    // than the duck simply switching off.
    sim.duckVy = Math.min(sim.duckVy, -120);
  }
}

/** Advance the world by exactly one fixed timestep. */
export function step(sim) {
  // A finished run has a fixed length. Stepping past the landing must change
  // nothing at all, otherwise two frame rates would disagree on the final tick
  // count purely because they noticed the death on different frames.
  if (sim.state === STATE.DEAD) return sim;

  sim.prevDuckY = sim.duckY;
  sim.prevScrollX = sim.scrollX;

  // Any input scheduled for this tick or earlier fires now, before physics.
  let flapped = false;
  while (sim.pending.length && sim.pending[0] <= sim.tick) {
    sim.pending.shift();
    if (sim.state === STATE.READY) {
      sim.state = STATE.PLAYING;
      sim.playStartTick = sim.tick;
      applyFlap(sim);
      flapped = true;
    } else if (sim.state === STATE.PLAYING && !flapped) {
      applyFlap(sim);
      flapped = true;
    }
  }

  if (sim.state === STATE.READY) {
    sim.tick += 1;
    return sim;
  }

  // Gravity, clamped at terminal fall speed.
  sim.duckVy = Math.min(sim.duckVy + CONFIG.gravity * SIM_DT, CONFIG.terminalFall);
  sim.duckY += sim.duckVy * SIM_DT;

  if (sim.duckY < CONFIG.ceilingY) {
    sim.duckY = CONFIG.ceilingY;
    if (sim.duckVy < 0) sim.duckVy = 0;
  }

  if (sim.state === STATE.PLAYING) {
    sim.scrollTicks += 1;
    sim.scrollX = sim.scrollTicks * SIM_DT * CONFIG.scrollSpeed;

    // Score the moment the duck's centre passes the obstacle's centre.
    const duckCenter = CONFIG.duckX + CONFIG.duckW / 2;
    while (obstacleScreenX(sim, sim.nextScoreIndex) + CONFIG.tileW / 2 <= duckCenter) {
      sim.nextScoreIndex += 1;
      sim.score += 1;
      sim.events.push({ type: 'score', tick: sim.tick, score: sim.score });
    }

    const box = duckHitbox(sim.duckY);
    const { first, last } = visibleRange(sim);
    for (let i = first; i <= last; i += 1) {
      const ob = obstacleAt(sim, i);
      if (ob.x > box.x + box.w || ob.x + CONFIG.tileW < box.x) continue;
      const hitTop = overlaps(box, ob.x, -200, CONFIG.tileW, ob.gapTop + 200);
      const hitBottom = overlaps(
        box, ob.x, ob.gapBottom, CONFIG.tileW, CONFIG.groundY - ob.gapBottom);
      if (hitTop || hitBottom) {
        die(sim, CAUSE.OBSTACLE);
        break;
      }
    }
  }

  // The ground ends the run, and also ends the death fall.
  const box = duckHitbox(sim.duckY);
  if (box.y + box.h >= CONFIG.groundY) {
    sim.duckY = CONFIG.groundY - CONFIG.duckH + (CONFIG.duckH - CONFIG.hitH) / 2;
    sim.duckVy = 0;
    if (sim.state === STATE.PLAYING) {
      die(sim, CAUSE.GROUND);
      sim.state = STATE.DEAD;
      sim.events.push({ type: 'landed', tick: sim.tick });
    } else if (sim.state === STATE.DYING) {
      sim.state = STATE.DEAD;
      sim.events.push({ type: 'landed', tick: sim.tick });
    }
  }

  sim.tick += 1;
  return sim;
}

/** Milliseconds of actual play, measured in simulation time. */
export function durationMs(sim) {
  if (sim.playStartTick < 0) return 0;
  const end = sim.deathTick >= 0 ? sim.deathTick : sim.tick;
  return Math.round((end - sim.playStartTick) * CONFIG.stepMs);
}

/**
 * Replay a recorded run. Feeding this the seed and flap trace from a
 * submission is how a score gets verified, and it is how the determinism
 * tests compare one frame rate against another.
 */
export function replay(seed, flaps, maxTicks = 200000) {
  const sim = createSim(seed);
  let next = 0;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    while (next < flaps.length && flaps[next] <= sim.tick) {
      queueFlap(sim, flaps[next]);
      next += 1;
    }
    step(sim);
  }
  return sim;
}

/** A compact, comparable fingerprint of the whole run. */
export function snapshot(sim) {
  return {
    tick: sim.tick,
    state: sim.state,
    score: sim.score,
    duckY: sim.duckY,
    duckVy: sim.duckVy,
    scrollX: sim.scrollX,
    deathTick: sim.deathTick,
    cause: sim.cause,
    flaps: sim.flaps.slice(),
    gaps: sim.gaps.slice(),
  };
}
