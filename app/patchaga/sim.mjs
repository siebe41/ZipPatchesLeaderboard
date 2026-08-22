/**
 * The Patchaga simulation.
 *
 * This module is pure: no DOM, no canvas, no timers, no Math.random, no wall
 * clock. Given a seed and a list of input events, it produces exactly the same
 * run every time, on any machine, at any frame rate. That property is the whole
 * reason the loop is built this way, and it is what lets the server replay a
 * submitted run in Python instead of believing the score it was handed.
 *
 * The caller advances the world by calling step() exactly once per fixed
 * timestep. Rendering interpolates between the previous and current position of
 * anything that moves and never writes back into these numbers.
 *
 * Positions are integers in sub-units (see config.mjs). Angles are integer
 * steps around a table-driven circle. Every division floors. Nothing here calls
 * a transcendental function. Those four rules are what keep this file and
 * patchaga.py agreeing tick for tick, and tools/check_patchaga_parity.py fails
 * the moment they stop.
 *
 * None of the artwork, the enemies or the names come from any existing arcade
 * game. It is a fixed shooter, which is a genre, built out of Patch My PC's own
 * material: a rubber duck firing patches at the bugs it is there to fix.
 */
import {
  CONFIG, FORM_LEFT, SIN_SCALE, SIN_STEPS,
  isin, icos, fdiv, tierFor, isSweepWave,
} from './config.mjs';
import { makeRng, rngInt } from './rng.mjs';

const U = CONFIG.unit;

/** Half a turn, used by the entry and sweep arcs. */
const SIN_HALF_STEPS = SIN_STEPS / 2;

export const STATE = {
  READY: 'ready',       // "WAVE n", nothing on screen yet
  PLAYING: 'playing',
  DYING: 'dying',
  CLEAR: 'clear',       // wave beaten, celebrating
  DEAD: 'dead',         // out of lives, the run is over
};

/** What a bug is. Only a rootkit can fork the duck. */
export const KIND = { DRONE: 0, WEEVIL: 1, ROOTKIT: 2 };
const KIND_NAMES = ['drone', 'weevil', 'rootkit'];

export const BUG = {
  WAITING: 0,    // queued offscreen, not yet launched
  ENTERING: 1,   // flying its entry path into the formation
  SLOT: 2,       // sitting in the formation
  DIVING: 3,     // peeled off and attacking
  BEAMING: 4,    // a rootkit hovering with its beam open
  RETURNING: 5,  // flying back to its slot after a dive
  SWEEPING: 6,   // crossing the screen on a regression sweep
  DEAD: 7,
};

/**
 * The four things a player can do.
 *
 * These are *edges*, not held states: the trace records the moment a direction
 * was taken or released and the moment fire was pressed. Recording held state
 * per tick instead would make the trace two orders of magnitude larger for the
 * same run and tell the replay nothing extra.
 */
export const ACTION = { LEFT: 0, RIGHT: 1, NEUTRAL: 2, FIRE: 3 };

/** Straight down, in table steps, for anything the bugs fire. */
const DOWN = SIN_STEPS / 2;

const PX = (px) => px * U;

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

function sign(v) {
  return v > 0 ? 1 : (v < 0 ? -1 : 0);
}

/** Axis-aligned overlap test. Half-extents arrive in pixels, positions in sub-units. */
function hits(ax, ay, ahw, ahh, bx, by, bhw, bhh) {
  return Math.abs(ax - bx) <= PX(ahw + bhw) && Math.abs(ay - by) <= PX(ahh + bhh);
}

// --------------------------------------------------------------------------
// The formation
// --------------------------------------------------------------------------
//
// The formation is not stored. It is a function of the tick, so every bug
// sitting in it derives its position from its slot rather than being pushed
// around, and a bug that rejoins after a dive cannot end up half a pixel out of
// line with its neighbours.

/** Where the centre of column `col` is at tick `t`, in sub-units. */
export function formX(col, t) {
  const sway = fdiv(PX(CONFIG.swayAmp) * isin(fdiv(t * SIN_STEPS, CONFIG.swayPeriod)),
    SIN_SCALE);
  const breathe = fdiv(PX(CONFIG.breatheAmp) * isin(fdiv(t * SIN_STEPS, CONFIG.breathePeriod)),
    SIN_SCALE);
  // Half-steps from the middle column, so the outer columns breathe furthest
  // and the arithmetic stays whole.
  const offset = col * 2 - (CONFIG.formCols - 1);
  return PX(FORM_LEFT + col * CONFIG.colStep) + sway + fdiv(offset * breathe, CONFIG.formCols - 1);
}

/** Where row `row` sits, in sub-units. Rows do not move. */
export function formY(row) {
  return PX(CONFIG.formTop + row * CONFIG.rowStep);
}

/** The kind of bug that belongs in a given formation row and column. */
function kindFor(row, col) {
  if (row === 0) return CONFIG.rootkitCols.includes(col) ? KIND.ROOTKIT : -1;
  return row <= 2 ? KIND.WEEVIL : KIND.DRONE;
}

// --------------------------------------------------------------------------
// Building a run
// --------------------------------------------------------------------------

function makeDuck() {
  return {
    x: PX(fdiv(CONFIG.width, 2)),
    dir: 0,          // -1, 0 or 1; the held steering state
    alive: true,
    merged: false,
    cooldown: 0,
    invuln: 0,       // ticks of grace after losing a merge
  };
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
    wave: 1,
    nextExtraLife: CONFIG.extraLifeAt,

    duck: makeDuck(),
    prevDuckX: PX(fdiv(CONFIG.width, 2)),

    bugs: [],
    patches: [],
    bugShots: [],
    rescue: null,     // the freed duck falling back to its owner

    launchIndex: 0,   // how many bugs have been sent on their entry path
    launchTimer: 0,
    diveTimer: 0,
    divesSinceRootkit: 0,
    sweepGroup: 0,
    sweepTimer: 0,
    sweepHits: 0,
    sweepTotal: 0,

    // Totals worth keeping for the board and the player page.
    bugsPatched: 0,
    wavesCleared: 0,
    forks: 0,
    rescues: 0,
    shotsFired: 0,

    playStartTick: -1,
    endTick: -1,

    pending: [],      // inputs queued but not yet reached
    inputs: [],       // every input actually applied. This is the trace.
    events: [],       // drained by the presentation layer; never read back here
  };
  buildWave(sim);
  return sim;
}

/** Lay out the wave the run is currently on, with every bug still offscreen. */
function buildWave(sim) {
  sim.bugs = [];
  sim.launchIndex = 0;
  sim.launchTimer = 0;
  sim.diveTimer = CONFIG.entrySettle;
  sim.divesSinceRootkit = CONFIG.rootkitEvery; // the first rootkit comes early
  sim.sweepGroup = 0;
  sim.sweepTimer = 0;
  sim.sweepHits = 0;
  sim.sweepTotal = 0;

  if (isSweepWave(sim.wave)) {
    sim.sweepTotal = CONFIG.sweepGroups * CONFIG.sweepGroupSize;
    return;
  }

  // Entry order runs bottom row first, so the back of the formation fills in
  // behind the bugs already in place rather than through them.
  let order = 0;
  for (let row = CONFIG.formRows - 1; row >= 0; row--) {
    for (let col = 0; col < CONFIG.formCols; col++) {
      const kind = kindFor(row, col);
      if (kind < 0) continue;
      sim.bugs.push(makeBug(sim, kind, col, row, order));
      order++;
    }
  }
}

function makeBug(sim, kind, col, row, order) {
  // Four entry routes, alternating so the screen fills from both sides at once.
  const route = order % 4;
  const fromLeft = route === 0 || route === 2;
  const fromTop = route < 2;
  return {
    kind,
    col,
    row,
    order,
    state: BUG.WAITING,
    x: PX(fromLeft ? -30 : CONFIG.width + 30),
    y: PX(fromTop ? -24 : CONFIG.height + 24),
    entryX: PX(fromLeft ? -30 : CONFIG.width + 30),
    entryY: PX(fromTop ? -24 : CONFIG.height + 24),
    bulgeX: PX(fromLeft ? 96 : -96),
    bulgeY: PX(fromTop ? 130 : -130),
    vx: 0,
    vy: 0,
    t: 0,             // ticks in the current bug state
    diveSide: 0,
    divePhase: 0,
    fireTimer: 0,
    beamOpen: false,
    wantsFork: false,
    holdsDuck: false,
    returnX: 0,
    returnY: 0,
    // Sweep bugs are created mid-wave and removed when they leave, unlike
    // formation bugs which are built once and stay in the array. An explicit
    // flag says which is which, rather than testing whether a field exists.
    isSweep: false,
    sweepFromLeft: false,
    sweepLane: 0,
    sweepPhase: 0,
  };
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
 *
 * The encoding is `tick * 4 + action`, matching PatchMan's trace format, so the
 * server's trace reader and its interval statistics work on both games without
 * a second implementation.
 */
export function queueInput(sim, atTick, action) {
  const t = Math.max(sim.tick, Math.floor(atTick));
  if (t > CONFIG.absoluteMaxTicks) return;
  if (sim.inputs.length + sim.pending.length >= CONFIG.maxInputTrace) return;
  sim.pending.push(t * 4 + action);
}

/** Apply everything queued for the tick about to run. */
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
  const duck = sim.duck;
  if (action === ACTION.LEFT) duck.dir = -1;
  else if (action === ACTION.RIGHT) duck.dir = 1;
  else if (action === ACTION.NEUTRAL) duck.dir = 0;
  else if (action === ACTION.FIRE) fire(sim);

  // The first input is what starts the run, so a player who never touches the
  // keyboard never starts a clock the server will later measure them against.
  if (sim.state === STATE.READY && sim.stateTick >= CONFIG.readyTicks) {
    sim.state = STATE.PLAYING;
    sim.stateTick = 0;
  }
}

function fire(sim) {
  const duck = sim.duck;
  if (!duck.alive || sim.state !== STATE.PLAYING) return;
  if (duck.cooldown > 0) return;
  const cap = duck.merged ? CONFIG.maxPatchesMerged : CONFIG.maxPatches;
  if (sim.patches.length >= cap) return;

  duck.cooldown = CONFIG.patchCooldown;
  const y = PX(CONFIG.duckY - CONFIG.duckHalfH);
  if (duck.merged) {
    sim.patches.push({ x: duck.x - PX(CONFIG.mergedOffset), y });
    sim.patches.push({ x: duck.x + PX(CONFIG.mergedOffset), y });
    sim.shotsFired += 2;
  } else {
    sim.patches.push({ x: duck.x, y });
    sim.shotsFired += 1;
  }
  sim.events.push({ type: 'fire' });
}

// --------------------------------------------------------------------------
// The step
// --------------------------------------------------------------------------

export function step(sim) {
  if (sim.state === STATE.DEAD) return;

  sim.prevDuckX = sim.duck.x;
  for (const b of sim.bugs) { b.px = b.x; b.py = b.y; }
  for (const p of sim.patches) { p.py = p.y; }
  for (const s of sim.bugShots) { s.px = s.x; s.py = s.y; }

  drainInput(sim);

  sim.stateTick++;

  if (sim.state === STATE.READY) {
    // The wave title holds until the player does something, so a run never
    // starts without them. After that the first input promotes it to PLAYING.
    if (sim.stateTick > CONFIG.readyTicks * 4) {
      sim.state = STATE.PLAYING;
      sim.stateTick = 0;
    }
  } else if (sim.state === STATE.PLAYING) {
    if (sim.playStartTick < 0) sim.playStartTick = sim.tick;
    stepPlaying(sim);
  } else if (sim.state === STATE.DYING) {
    stepBugs(sim);
    if (sim.stateTick >= CONFIG.deathTicks) respawn(sim);
  } else if (sim.state === STATE.CLEAR) {
    if (sim.stateTick >= CONFIG.clearTicks) nextWave(sim);
  }

  sim.tick++;

  // A run cannot outlast the ceiling both engines share. Reaching it ends the
  // run rather than truncating it, so the server and the browser agree about
  // what happened at the boundary.
  if (sim.tick >= CONFIG.absoluteMaxTicks && sim.state !== STATE.DEAD) {
    endRun(sim);
  }
}

function stepPlaying(sim) {
  stepDuck(sim);
  stepPatches(sim);
  if (isSweepWave(sim.wave)) stepSweep(sim); else stepFormation(sim);
  stepBugs(sim);
  stepBugShots(sim);
  stepRescue(sim);
  collide(sim);
  checkWaveOver(sim);
}

// --- The duck -------------------------------------------------------------- #

function stepDuck(sim) {
  const duck = sim.duck;
  if (duck.cooldown > 0) duck.cooldown--;
  if (duck.invuln > 0) duck.invuln--;
  if (!duck.alive) return;

  const half = CONFIG.duckHalfW + (duck.merged ? CONFIG.mergedOffset : 0);
  const lo = PX(CONFIG.duckMargin + half);
  const hi = PX(CONFIG.width - CONFIG.duckMargin - half);
  duck.x = clamp(duck.x + duck.dir * CONFIG.duckSpeed, lo, hi);
}

function stepPatches(sim) {
  for (let i = sim.patches.length - 1; i >= 0; i--) {
    const p = sim.patches[i];
    p.y -= CONFIG.patchSpeed;
    if (p.y < PX(CONFIG.hudTop - 8)) sim.patches.splice(i, 1);
  }
}

function stepBugShots(sim) {
  for (let i = sim.bugShots.length - 1; i >= 0; i--) {
    const s = sim.bugShots[i];
    s.x += s.vx;
    s.y += s.vy;
    if (s.y > PX(CONFIG.floorY) || s.x < -PX(12) || s.x > PX(CONFIG.width + 12)) {
      sim.bugShots.splice(i, 1);
    }
  }
}

// --- Wave pacing ----------------------------------------------------------- #

function stepFormation(sim) {
  // Launch the entry paths on a stagger.
  if (sim.launchIndex < sim.bugs.length) {
    if (sim.launchTimer <= 0) {
      const bug = sim.bugs[sim.launchIndex];
      bug.state = BUG.ENTERING;
      bug.t = 0;
      sim.launchIndex++;
      sim.launchTimer = CONFIG.entryStagger;
      sim.events.push({ type: 'enter' });
    } else {
      sim.launchTimer--;
    }
    return; // nothing dives while the formation is still arriving
  }

  const tier = tierFor(sim.wave);
  if (sim.diveTimer > 0) { sim.diveTimer--; return; }

  const diving = sim.bugs.filter((b) =>
    b.state === BUG.DIVING || b.state === BUG.BEAMING).length;
  const cap = Math.min(tier.divers, CONFIG.maxDiversCap);
  if (diving >= cap) return;

  const ready = sim.bugs.filter((b) => b.state === BUG.SLOT);
  if (!ready.length) return;

  // Rootkits are rare enough that a uniform pick would hide the fork. Once
  // enough dives have gone by without one, the next diver is a rootkit if one
  // is available -- and the counter only resets when a rootkit actually goes,
  // so a wave whose rootkits are all dead does not stall waiting for them.
  let pool = ready;
  if (sim.divesSinceRootkit >= CONFIG.rootkitEvery) {
    const rootkits = ready.filter((b) => b.kind === KIND.ROOTKIT);
    if (rootkits.length) pool = rootkits;
  }

  const bug = pool[rngInt(sim.rng, pool.length)];
  if (bug.kind === KIND.ROOTKIT) sim.divesSinceRootkit = 0;
  else sim.divesSinceRootkit++;

  launchDive(sim, bug, tier);
  sim.diveTimer = Math.max(30,
    CONFIG.diveGapMin + tier.diveGap + rngInt(sim.rng, CONFIG.diveGapSpread));
}

function launchDive(sim, bug, tier) {
  bug.state = BUG.DIVING;
  bug.t = 0;
  bug.diveSide = rngInt(sim.rng, 2) === 0 ? -1 : 1;
  bug.divePhase = rngInt(sim.rng, SIN_STEPS);
  bug.fireTimer = CONFIG.fireEvery;
  bug.vx = 0;
  bug.vy = 0;
  // A rootkit that still has both hands free may go for the fork instead of
  // the kill. One that is already carrying a duck never does.
  bug.beamOpen = false;
  bug.wantsFork = bug.kind === KIND.ROOTKIT
    && !bug.holdsDuck
    && sim.duck.alive
    && rngInt(sim.rng, 100) < CONFIG.forkChance;
  sim.events.push({ type: 'dive', kind: KIND_NAMES[bug.kind] });
}

function stepSweep(sim) {
  // A regression sweep has no formation: groups cross the screen, nothing
  // shoots, and the reward is for clearing every last one.
  if (sim.sweepGroup >= CONFIG.sweepGroups) return;
  if (sim.sweepTimer > 0) { sim.sweepTimer--; return; }

  const fromLeft = sim.sweepGroup % 2 === 0;
  const lane = 120 + rngInt(sim.rng, 120);
  for (let i = 0; i < CONFIG.sweepGroupSize; i++) {
    const bug = makeBug(sim, sim.sweepGroup % 3, i, 0, i);
    bug.state = BUG.SWEEPING;
    bug.t = -i * 14;                     // a trailing line rather than a block
    bug.isSweep = true;
    bug.sweepFromLeft = fromLeft;
    bug.sweepLane = lane;
    bug.sweepPhase = rngInt(sim.rng, SIN_STEPS);
    bug.x = PX(fromLeft ? -24 : CONFIG.width + 24);
    bug.y = PX(lane);
    sim.bugs.push(bug);
  }
  sim.sweepGroup++;
  sim.sweepTimer = CONFIG.sweepGap;
  sim.events.push({ type: 'sweep' });
}

// --- Bug motion ------------------------------------------------------------ #

function stepBugs(sim) {
  // Backwards, and it matters. A beaming bug can capture the duck from inside
  // this loop, and losing the duck sends every diver home -- so the bugs already
  // stepped this tick keep the state they were given, and the ones not yet
  // reached are stepped again in their new state. Iterating the other way splits
  // that set differently and quietly produces a different game, which is a
  // difference the server's replay would then reject a real run over.
  for (let i = sim.bugs.length - 1; i >= 0; i--) {
    const bug = sim.bugs[i];
    bug.t++;
    switch (bug.state) {
      case BUG.ENTERING: stepEntering(sim, bug); break;
      case BUG.SLOT: stepSlot(sim, bug); break;
      case BUG.DIVING: stepDiving(sim, bug); break;
      case BUG.BEAMING: stepBeaming(sim, bug); break;
      case BUG.RETURNING: stepReturning(sim, bug); break;
      case BUG.SWEEPING: stepSweeping(sim, bug); break;
      default: break;
    }
  }
  // Sweep bugs that have left are removed rather than parked, because a sweep
  // wave creates them as it goes and the array would otherwise only grow.
  for (let i = sim.bugs.length - 1; i >= 0; i--) {
    if (sim.bugs[i].state === BUG.DEAD && sim.bugs[i].isSweep) {
      sim.bugs.splice(i, 1);
    }
  }
}

function stepEntering(sim, bug) {
  const total = CONFIG.entryTicks;
  const t = Math.min(bug.t, total);
  // Progress as a scaled fraction, and a half-sine that starts and ends at zero
  // so the arc bulges out in the middle and lands exactly on the slot.
  const f = fdiv(t * SIN_SCALE, total);
  const bulge = isin(fdiv(f * SIN_HALF_STEPS, SIN_SCALE));
  const tx = formX(bug.col, sim.tick);
  const ty = formY(bug.row);
  bug.x = bug.entryX + fdiv((tx - bug.entryX) * f, SIN_SCALE) + fdiv(bug.bulgeX * bulge, SIN_SCALE);
  bug.y = bug.entryY + fdiv((ty - bug.entryY) * f, SIN_SCALE) + fdiv(bug.bulgeY * bulge, SIN_SCALE);
  if (bug.t >= total) {
    bug.state = BUG.SLOT;
    bug.t = 0;
  }
}

function stepSlot(sim, bug) {
  bug.x = formX(bug.col, sim.tick);
  bug.y = formY(bug.row);
}

function stepDiving(sim, bug) {
  const tier = tierFor(sim.wave);
  const fall = fdiv(CONFIG.diveFall * tier.speed, 100);
  bug.vy = bug.t < CONFIG.diveEaseTicks
    ? fdiv(fall * bug.t, CONFIG.diveEaseTicks)
    : fall;

  const angle = fdiv(bug.t * SIN_STEPS, CONFIG.diveSwingPeriod) + bug.divePhase;
  bug.vx = fdiv(CONFIG.diveSwingSpeed * isin(angle), SIN_SCALE) * bug.diveSide;
  if (bug.t > CONFIG.diveHomeAfter && sim.duck.alive) {
    bug.vx += sign(sim.duck.x - bug.x) * fdiv(CONFIG.diveHomePull * tier.speed, 100);
  }

  bug.x += bug.vx;
  bug.y += bug.vy;

  // A fork attempt stops at hover height and opens the beam instead of
  // continuing into the duck.
  if (bug.wantsFork && bug.y >= PX(CONFIG.beamHoverY)) {
    bug.state = BUG.BEAMING;
    bug.t = 0;
    bug.beamOpen = true;
    bug.wantsFork = false;
    sim.events.push({ type: 'beam' });
    return;
  }

  maybeFire(sim, bug, tier);

  if (bug.y > PX(CONFIG.height + 30)) {
    // Off the bottom and around to the top, which is where the return leg
    // starts. Wrapping horizontally keeps it near the column it left from.
    bug.state = BUG.RETURNING;
    bug.t = 0;
    bug.returnX = clamp(bug.x, PX(20), PX(CONFIG.width - 20));
    bug.returnY = PX(-26);
    bug.x = bug.returnX;
    bug.y = bug.returnY;
  }
}

function stepBeaming(sim, bug) {
  // Hold station with a slow drift, so a beaming rootkit is a target rather
  // than a fixture.
  bug.vx = fdiv(CONFIG.diveSwingSpeed * isin(fdiv(bug.t * SIN_STEPS, 360)), SIN_SCALE * 3);
  bug.x = clamp(bug.x + bug.vx, PX(24), PX(CONFIG.width - 24));

  const duck = sim.duck;
  if (bug.t > CONFIG.beamWindup && duck.alive && !bug.holdsDuck) {
    if (Math.abs(duck.x - bug.x) <= PX(CONFIG.beamHalfW)) {
      forkDuck(sim, bug);
    }
  }

  if (bug.t >= CONFIG.beamTicks) {
    bug.beamOpen = false;
    bug.state = BUG.DIVING;
    bug.t = CONFIG.diveEaseTicks; // resume at full speed, not from a standstill
  }
}

function stepReturning(sim, bug) {
  const total = CONFIG.reentryTicks;
  const t = Math.min(bug.t, total);
  const f = fdiv(t * SIN_SCALE, total);
  const tx = formX(bug.col, sim.tick);
  const ty = formY(bug.row);
  bug.x = bug.returnX + fdiv((tx - bug.returnX) * f, SIN_SCALE);
  bug.y = bug.returnY + fdiv((ty - bug.returnY) * f, SIN_SCALE);
  if (bug.t >= total) {
    bug.state = BUG.SLOT;
    bug.t = 0;
  }
}

function stepSweeping(sim, bug) {
  if (bug.t < 0) return;
  const tier = tierFor(sim.wave);
  const speed = fdiv(150 * tier.speed, 100);
  bug.vx = bug.sweepFromLeft ? speed : -speed;
  // A shallow S through the lane, which is what makes a sweep worth aiming at
  // rather than holding the fire button down for.
  bug.vy = fdiv(46 * isin(fdiv(bug.t * SIN_STEPS, 200) + bug.sweepPhase), SIN_SCALE);
  bug.x += bug.vx;
  bug.y += bug.vy;
  if (bug.x < -PX(40) || bug.x > PX(CONFIG.width + 40)) {
    bug.state = BUG.DEAD;
  }
}

function maybeFire(sim, bug, tier) {
  if (isSweepWave(sim.wave)) return;      // a sweep never shoots back
  if (!sim.duck.alive) return;
  if (sim.bugShots.length >= CONFIG.maxBugShots) return;
  if (bug.fireTimer > 0) { bug.fireTimer--; return; }

  bug.fireTimer = CONFIG.fireEvery;
  const chance = fdiv(CONFIG.fireChance * tier.fire, 100);
  if (rngInt(sim.rng, 100) >= chance) return;

  // Lean the shot toward the duck, capped, so a bug directly overhead is
  // dangerous and one across the screen is not sniping.
  const lean = clamp(fdiv(sim.duck.x - bug.x, 96),
    -CONFIG.bugShotSpread, CONFIG.bugShotSpread);
  sim.bugShots.push({
    x: bug.x,
    y: bug.y + PX(CONFIG.bugHalfH),
    vx: fdiv(CONFIG.bugShotSpeed * isin(lean), SIN_SCALE),
    vy: fdiv(CONFIG.bugShotSpeed * icos(lean), SIN_SCALE),
  });
  sim.events.push({ type: 'bugfire' });
}

// --- The fork and the rescue ----------------------------------------------- #

function forkDuck(sim, bug) {
  bug.holdsDuck = true;
  bug.beamOpen = false;
  bug.state = BUG.DIVING;
  bug.t = 0;
  sim.forks++;
  sim.events.push({ type: 'fork' });
  loseDuck(sim, true);
}

function stepRescue(sim) {
  const r = sim.rescue;
  if (!r) return;
  r.y += CONFIG.rescueDropSpeed;
  if (r.y >= PX(CONFIG.duckY) && sim.duck.alive) {
    sim.rescue = null;
    if (!sim.duck.merged) {
      sim.duck.merged = true;
      addScore(sim, CONFIG.mergeBonus);
      sim.rescues++;
      sim.events.push({ type: 'merge' });
    }
    return;
  }
  if (r.y > PX(CONFIG.height + 20)) sim.rescue = null;
}

// --- Damage ---------------------------------------------------------------- #

/**
 * The duck was hit, or caught.
 *
 * Being hit while merged costs the merge rather than a life. That is the point
 * of the rescue: it buys one mistake back, and losing it is loud enough that
 * the player knows what it cost them.
 */
function loseDuck(sim, forked) {
  const duck = sim.duck;
  if (!duck.alive || duck.invuln > 0) return;

  if (duck.merged && !forked) {
    duck.merged = false;
    duck.invuln = 90;
    sim.events.push({ type: 'unmerge' });
    return;
  }

  duck.alive = false;
  duck.merged = false;
  duck.dir = 0;
  sim.lives--;
  sim.bugShots.length = 0;
  sim.events.push({ type: 'die' });

  // Everything in the air goes home. Resuming into a half-finished dive the
  // player never saw start is the kind of unfair that reads as a bug.
  for (const b of sim.bugs) {
    if (b.state === BUG.DIVING || b.state === BUG.BEAMING) {
      b.beamOpen = false;
      b.wantsFork = false;
      b.state = BUG.RETURNING;
      b.t = 0;
      b.returnX = clamp(b.x, PX(20), PX(CONFIG.width - 20));
      b.returnY = b.y;
    }
  }

  if (sim.lives <= 0) {
    endRun(sim);
  } else {
    sim.state = STATE.DYING;
    sim.stateTick = 0;
  }
}

function respawn(sim) {
  sim.duck = makeDuck();
  sim.duck.invuln = CONFIG.respawnTicks;
  sim.state = STATE.PLAYING;
  sim.stateTick = 0;
  sim.events.push({ type: 'respawn' });
}

function endRun(sim) {
  if (sim.state === STATE.DEAD) return;
  sim.state = STATE.DEAD;
  sim.stateTick = 0;
  sim.endTick = sim.tick;
  sim.events.push({ type: 'gameover' });
}

// --- Collisions ------------------------------------------------------------ #

function collide(sim) {
  const duck = sim.duck;

  // Patches against bugs.
  for (let pi = sim.patches.length - 1; pi >= 0; pi--) {
    const p = sim.patches[pi];
    let hit = -1;
    for (let bi = 0; bi < sim.bugs.length; bi++) {
      const b = sim.bugs[bi];
      if (b.state === BUG.DEAD || b.state === BUG.WAITING) continue;
      if (b.state === BUG.SWEEPING && b.t < 0) continue;
      if (hits(p.x, p.y, CONFIG.patchHalfW, CONFIG.patchHalfH,
        b.x, b.y, CONFIG.bugHalfW, CONFIG.bugHalfH)) {
        hit = bi;
        break;
      }
    }
    if (hit < 0) continue;
    sim.patches.splice(pi, 1);
    killBug(sim, sim.bugs[hit]);
  }

  if (!duck.alive) return;

  const half = CONFIG.duckHalfW + (duck.merged ? CONFIG.mergedOffset : 0);

  // What the bugs fired.
  for (let i = sim.bugShots.length - 1; i >= 0; i--) {
    const s = sim.bugShots[i];
    if (hits(s.x, s.y, CONFIG.bugShotHalfW, CONFIG.bugShotHalfH,
      duck.x, PX(CONFIG.duckY), half, CONFIG.duckHalfH)) {
      sim.bugShots.splice(i, 1);
      loseDuck(sim, false);
      return;
    }
  }

  // The bugs themselves. Only something that has left the formation can touch
  // the duck, which is what makes the bottom of the screen safe to sit in
  // until it very suddenly is not.
  for (const b of sim.bugs) {
    if (b.state !== BUG.DIVING && b.state !== BUG.BEAMING && b.state !== BUG.SWEEPING) continue;
    if (b.state === BUG.SWEEPING && b.t < 0) continue;
    if (hits(b.x, b.y, CONFIG.bugHalfW, CONFIG.bugHalfH,
      duck.x, PX(CONFIG.duckY), half, CONFIG.duckHalfH)) {
      loseDuck(sim, false);
      return;
    }
  }
}

function killBug(sim, bug) {
  const diving = bug.state === BUG.DIVING
    || bug.state === BUG.BEAMING
    || bug.state === BUG.SWEEPING;
  const table = CONFIG.points[KIND_NAMES[bug.kind]];
  const points = diving ? table.diving : table.still;
  addScore(sim, points);
  sim.bugsPatched++;

  if (bug.state === BUG.SWEEPING) sim.sweepHits++;

  // A rootkit carrying a duck gives it back only if it is shot down away from
  // the formation. Shot while parked, it takes the duck with it -- which is
  // what makes waiting for it to come to you the right call.
  if (bug.holdsDuck) {
    if (diving) {
      sim.rescue = { x: bug.x, y: bug.y };
      sim.events.push({ type: 'freed' });
    } else {
      sim.events.push({ type: 'lostfork' });
    }
    bug.holdsDuck = false;
  }

  bug.state = BUG.DEAD;
  bug.beamOpen = false;
  sim.events.push({
    type: 'pop', kind: KIND_NAMES[bug.kind], x: bug.x, y: bug.y, points,
  });
}

function addScore(sim, points) {
  sim.score = Math.min(sim.score + points, CONFIG.maxScore);
  while (sim.score >= sim.nextExtraLife && sim.lives < CONFIG.maxLives) {
    sim.lives++;
    sim.nextExtraLife += CONFIG.extraLifeEvery;
    sim.events.push({ type: 'extralife' });
  }
  // Past the cap on lives the threshold still has to advance, or every point
  // scored afterwards would re-trigger the award test forever.
  while (sim.score >= sim.nextExtraLife) {
    sim.nextExtraLife += CONFIG.extraLifeEvery;
  }
}

// --- Wave completion ------------------------------------------------------- #

function checkWaveOver(sim) {
  if (isSweepWave(sim.wave)) {
    const done = sim.sweepGroup >= CONFIG.sweepGroups
      && !sim.bugs.some((b) => b.state === BUG.SWEEPING);
    if (!done) return;
    addScore(sim, sim.sweepHits * CONFIG.sweepPerBug);
    // The total guard matters: without it a sweep that somehow launched no bugs
    // pays the perfect bonus for doing nothing, because 0 >= 0.
    if (sim.sweepTotal > 0 && sim.sweepHits >= sim.sweepTotal) {
      addScore(sim, CONFIG.sweepPerfect);
      sim.events.push({ type: 'perfect' });
    }
    sim.wavesCleared++;
    sim.state = STATE.CLEAR;
    sim.stateTick = 0;
    return;
  }

  if (sim.bugs.some((b) => b.state !== BUG.DEAD)) return;
  addScore(sim, CONFIG.waveBonus * sim.wave);
  sim.wavesCleared++;
  sim.state = STATE.CLEAR;
  sim.stateTick = 0;
  sim.events.push({ type: 'clear' });
}

function nextWave(sim) {
  sim.wave++;
  sim.patches.length = 0;
  sim.bugShots.length = 0;
  sim.rescue = null;
  buildWave(sim);
  sim.state = STATE.PLAYING;
  sim.stateTick = 0;
}

// --------------------------------------------------------------------------
// Readings
// --------------------------------------------------------------------------

/**
 * How long the run lasted, in milliseconds of simulated time.
 *
 * Measured from the first input rather than from tick zero, because the wave
 * title sits on screen until the player does something and time spent there is
 * not time spent playing. The server compares this against its own clock, so
 * counting the wait would make a patient player look like a forgery.
 *
 * It is derived from ticks rather than measured, so a slow machine and a fast
 * one report the same run.
 */
export function durationMs(sim) {
  if (sim.playStartTick < 0) return 0;
  const end = sim.endTick >= 0 ? sim.endTick : sim.tick;
  return Math.round((end - sim.playStartTick) * CONFIG.stepMs);
}

/** True once the run is over and nothing further can change the score. */
export function isOver(sim) {
  return sim.state === STATE.DEAD;
}

/**
 * Whether a fire press would actually produce a patch right now.
 *
 * The simulation does not need this -- a press that cannot fire is simply
 * ignored -- but the presentation layer does, so it can avoid recording
 * thousands of presses that provably did nothing. The trace is a record of the
 * run, and a no-op does not belong in it.
 */
export function canFire(sim) {
  const duck = sim.duck;
  if (!duck.alive || sim.state !== STATE.PLAYING || duck.cooldown > 0) return false;
  return sim.patches.length < (duck.merged ? CONFIG.maxPatchesMerged : CONFIG.maxPatches);
}
