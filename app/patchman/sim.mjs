/**
 * The PatchMan simulation.
 *
 * Pure: no DOM, no canvas, no timers, no Math.random. Given a seed and the
 * list of direction changes the player made, it produces exactly the same run
 * every time, on any machine, at any frame rate. The server has a Python copy
 * of this file and replays every submitted run through it, so "exactly" here
 * means exactly, not approximately.
 *
 * Two decisions do most of the work of keeping that promise.
 *
 * Positions are integers in sub-units, never pixels and never floats. Sixty
 * four sub-units to a tile, so a tile centre is a whole number and "am I on a
 * centre" is `x % 64 === 32`, an equality rather than a tolerance. Turns and
 * collisions therefore happen at the same instant in both engines instead of
 * one of them rounding the other way on the four hundredth tile.
 *
 * Movement happens in hops to the next tile centre, not one sub-unit at a
 * time. An entity asks how far the next centre is, moves the smaller of that
 * and what it has left, and repeats. Two or three iterations per entity per
 * tick, whatever the speed, which is what makes a fifteen minute run cheap
 * enough for the server to replay on submission.
 */
import { CONFIG } from './config.mjs';
import { makeRng } from './rng.mjs';
import {
  COLS, ROWS, CELL, WORLD_W,
  UP, LEFT, DOWN, RIGHT, DX, DY, OPPOSITE,
  PATCH, LOGO, FLOOR,
  wrapCol, isWall, isDoor, isHouse, isTunnel,
  freshPatches, homeDistance,
  tileCenterX, tileCenterY, tileOfX, tileOfY, onCenter, stepToCenter,
} from './maze.mjs';

export const STATE = {
  IDLE: 'idle',         // waiting for the first input; the clock is not running
  READY: 'ready',       // board is up, everything held for a beat
  PLAYING: 'playing',
  DYING: 'dying',
  CLEAR: 'clear',       // last patch collected, board flashing
  DEAD: 'dead',
};

export const VULN = {
  HOUSE: 'house',
  LEAVING: 'leaving',
  OUT: 'out',
  EYES: 'eyes',
  ENTERING: 'entering',
};

const FAR = 1 << 30;

function wrapX(x) {
  return ((x % WORLD_W) + WORLD_W) % WORLD_W;
}

function tierOf(level) {
  const table = CONFIG.speedTier;
  const i = Math.min(level - 1, table.length - 1);
  return CONFIG.speeds[table[Math.max(0, i)]];
}

function frightTicksFor(level) {
  const table = CONFIG.frightenedTicks;
  return table[Math.min(level - 1, table.length - 1)];
}

function phasesFor(level) {
  return level >= CONFIG.phasesLateFromLevel ? CONFIG.phasesLate : CONFIG.phasesEarly;
}

function bonusFor(level) {
  return CONFIG.bonuses[Math.min(level - 1, CONFIG.bonuses.length - 1)];
}

function makeVuln(index) {
  return {
    index,
    x: 0,
    y: 0,
    prevX: 0,
    prevY: 0,
    dir: LEFT,
    state: VULN.HOUSE,
    fright: false,
    bob: 0,
    bobDir: 1,
  };
}

export function createSim(seed) {
  const sim = {
    seed: seed >>> 0,
    rng: makeRng(seed),
    tick: 0,
    state: STATE.IDLE,
    level: 1,
    score: 0,
    lives: CONFIG.lives,
    tiles: [],
    patchesLeft: 0,
    patchesEaten: 0,       // this level, which is what the house releases on
    totalPatches: 0,       // across the whole run, for the board's second column
    pac: {
      x: 0, y: 0, prevX: 0, prevY: 0, dir: LEFT, want: LEFT, mouth: 0,
    },
    vulns: [makeVuln(0), makeVuln(1), makeVuln(2), makeVuln(3)],
    phaseIndex: 0,
    phaseTicks: 0,
    phaseKind: 'scatter',
    frightTicks: 0,
    frightChain: 0,
    // Cumulative across the whole run, unlike frightChain which resets with
    // every window. It is what the scoreboard means by "vulnerabilities
    // patched", and it also gives the parity check something durable to
    // compare, since a chain that has already ended leaves no other trace.
    vulnsPatched: 0,
    elroyStage: 0,
    freezeTicks: 0,
    readyTicks: 0,
    deathTicks: 0,
    clearTicks: 0,
    houseIdle: 0,
    bonusState: 'none',    // none | up | eaten
    bonusTicks: 0,
    bonusesShown: 0,
    bonusFlash: 0,
    playStartTick: -1,
    endTick: -1,
    turns: [],             // the trace: tick * 4 + direction, ascending
    pending: [],
    lastQueuedDir: -1,
    events: [],
  };
  loadLevel(sim);
  return sim;
}

function loadLevel(sim) {
  const fresh = freshPatches();
  sim.tiles = fresh.tiles;
  sim.patchesLeft = fresh.count;
  sim.patchesEaten = 0;
  sim.bonusesShown = 0;
  sim.bonusState = 'none';
  sim.bonusTicks = 0;
  resetActors(sim);
}

function resetActors(sim) {
  const p = sim.pac;
  p.x = tileCenterX(CONFIG.startTile[0]);
  p.y = tileCenterY(CONFIG.startTile[1]);
  p.prevX = p.x;
  p.prevY = p.y;
  p.dir = LEFT;
  p.want = LEFT;

  const laneY = tileCenterY(CONFIG.houseLaneRow);
  for (let i = 0; i < 4; i += 1) {
    const g = sim.vulns[i];
    g.fright = false;
    g.bob = i * 8 - 12;
    g.bobDir = 1;
    if (i === 0) {
      // The first one is already loose. A board where nothing moves for the
      // first twenty seconds teaches the player the wrong thing.
      g.x = tileCenterX(CONFIG.doorCol);
      g.y = tileCenterY(CONFIG.houseExitRow);
      g.dir = LEFT;
      g.state = VULN.OUT;
    } else {
      g.x = tileCenterX(CONFIG.homeCols[i]);
      g.y = laneY + g.bob;
      g.dir = i === 2 ? UP : DOWN;
      g.state = VULN.HOUSE;
    }
    g.prevX = g.x;
    g.prevY = g.y;
  }

  sim.phaseIndex = 0;
  sim.phaseTicks = 0;
  sim.phaseKind = phasesFor(sim.level)[0][0];
  sim.frightTicks = 0;
  sim.frightChain = 0;
  sim.freezeTicks = 0;
  sim.houseIdle = 0;
  sim.readyTicks = CONFIG.readyTicks;
}

/**
 * Ask for a direction at a specific simulation tick.
 *
 * Input is quantised to the timestep rather than to the frame, so a run plays
 * out identically at 30, 60 and 144 Hz. Repeating the direction already asked
 * for is dropped here rather than recorded, which is what stops a held key
 * from filling the trace with thousands of identical entries.
 */
export function queueTurn(sim, atTick, dir) {
  if (dir < 0 || dir > 3) return;
  if (sim.lastQueuedDir === dir) return;
  sim.lastQueuedDir = dir;
  const t = Math.max(sim.tick, Math.floor(atTick));
  sim.pending.push(t * 4 + dir);
}

export function traceTick(code) {
  return Math.floor(code / 4);
}

export function traceDir(code) {
  return code - Math.floor(code / 4) * 4;
}

// --- Speeds -----------------------------------------------------------------

function pacSpeed(sim) {
  const s = tierOf(sim.level);
  return sim.frightTicks > 0 ? s.energized : s.patchman;
}

function vulnSpeed(sim, g) {
  const s = tierOf(sim.level);
  if (g.state === VULN.EYES || g.state === VULN.ENTERING) return s.eyes;
  if (g.fright) return s.frightened;
  if (isTunnel(tileOfX(g.x), tileOfY(g.y))) return s.tunnel;
  if (g.index === 0 && sim.elroyStage > 0) return s.elroy;
  return s.vuln;
}

// --- Targeting --------------------------------------------------------------

/**
 * Where a vulnerability would like to be standing.
 *
 * None of them path-find. Each one picks whichever legal turn puts it closest
 * to this tile in a straight line, which is cheap, produces no ties worth
 * arguing about, and — because the four of them want different tiles — makes
 * them behave like four different opponents rather than one opponent drawn
 * four times.
 */
function targetTile(sim, g) {
  const scatter = sim.phaseKind === 'scatter';
  const pc = tileOfX(sim.pac.x);
  const pr = tileOfY(sim.pac.y);
  const pd = sim.pac.dir;

  if (g.index === 0) {
    // Straight at you, and once the board is nearly clear it stops going home
    // to rest at all.
    if (scatter && sim.elroyStage < 2) return CONFIG.vulns[0].scatter;
    return [pc, pr];
  }
  if (scatter) return CONFIG.vulns[g.index].scatter;

  if (g.index === 1) {
    const n = CONFIG.ambushTiles;
    return [pc + DX[pd] * n, pr + DY[pd] * n];
  }
  if (g.index === 2) {
    // Reflects the leader through a point ahead of you, so it tends to arrive
    // from whichever side the leader is not on. Pincers, without a line of
    // code that knows what a pincer is.
    const n = CONFIG.flankTiles;
    const ax = pc + DX[pd] * n;
    const ay = pr + DY[pd] * n;
    const lead = sim.vulns[0];
    return [2 * ax - tileOfX(lead.x), 2 * ay - tileOfY(lead.y)];
  }
  // Bold at range, loses its nerve up close.
  const gc = tileOfX(g.x);
  const gr = tileOfY(g.y);
  const dx = gc - pc;
  const dy = gr - pr;
  if (dx * dx + dy * dy > CONFIG.timidTiles * CONFIG.timidTiles) return [pc, pr];
  return CONFIG.vulns[3].scatter;
}

function chooseDir(sim, g) {
  const c = tileOfX(g.x);
  const r = tileOfY(g.y);
  const back = OPPOSITE[g.dir];

  const opts = [];
  for (let d = 0; d < 4; d += 1) {
    if (d === back) continue;
    const nr = r + DY[d];
    if (nr < 0 || nr >= ROWS) continue;
    const nc = wrapCol(c + DX[d]);
    if (isWall(nc, nr) || isDoor(nc, nr) || isHouse(nc, nr)) continue;
    opts.push(d);
  }
  if (opts.length === 0) {
    g.dir = back;
    return;
  }
  if (opts.length === 1) {
    g.dir = opts[0];
    return;
  }

  if (g.fright) {
    // The only place the simulation draws a random number. Keeping it to one
    // call site means the RNG stream depends on nothing but how many junctions
    // a frightened vulnerability reached, which is trivial to match in another
    // language.
    g.dir = opts[Math.floor(sim.rng() * opts.length)];
    return;
  }

  if (g.state === VULN.EYES) {
    // Going home is the one journey that has to actually arrive, so it follows
    // a real distance field instead of guessing. Greedy targeting can circle a
    // block forever; counting steps cannot.
    let best = opts[0];
    let bestD = FAR;
    for (let i = 0; i < opts.length; i += 1) {
      const d = opts[i];
      const dist = homeDistance(c + DX[d], r + DY[d]);
      if (dist >= 0 && dist < bestD) {
        bestD = dist;
        best = d;
      }
    }
    g.dir = best;
    return;
  }

  const target = targetTile(sim, g);
  let best = opts[0];
  let bestD = FAR;
  for (let i = 0; i < opts.length; i += 1) {
    const d = opts[i];
    const dx = c + DX[d] - target[0];
    const dy = r + DY[d] - target[1];
    // Squared distance, never a square root: integers only, and the ordering
    // is the same. Ties fall to the lower direction index, which is why up,
    // left, down, right is the order the constants are numbered in.
    const dist = dx * dx + dy * dy;
    if (dist < bestD) {
      bestD = dist;
      best = d;
    }
  }
  g.dir = best;
}

// --- Movement ---------------------------------------------------------------

function pacCanGo(sim, dir) {
  const c = tileOfX(sim.pac.x);
  const r = tileOfY(sim.pac.y);
  const nr = r + DY[dir];
  if (nr < 0 || nr >= ROWS) return false;
  const nc = wrapCol(c + DX[dir]);
  return !isWall(nc, nr) && !isDoor(nc, nr) && !isHouse(nc, nr);
}

function movePac(sim) {
  const p = sim.pac;
  let remaining = pacSpeed(sim);

  // Turning back the way you came needs no junction. Anywhere, any time, which
  // is the difference between a maze that feels responsive and one that feels
  // like it is arguing with you.
  if (p.want === OPPOSITE[p.dir] && pacCanGo(sim, p.want)) {
    p.dir = p.want;
  }

  while (remaining > 0) {
    if (onCenter(p.x, p.y)) {
      if (p.want !== p.dir && pacCanGo(sim, p.want)) p.dir = p.want;
      if (!pacCanGo(sim, p.dir)) break;
    }
    const dx = DX[p.dir];
    const dy = DY[p.dir];
    const d = dx !== 0 ? stepToCenter(p.x, dx) : stepToCenter(p.y, dy);
    const m = d < remaining ? d : remaining;
    p.x = wrapX(p.x + dx * m);
    p.y += dy * m;
    remaining -= m;
    if (onCenter(p.x, p.y)) collect(sim);
  }
  p.mouth = (p.mouth + 1) % 1000000;
}

function moveVulnMaze(sim, g) {
  let remaining = vulnSpeed(sim, g);
  while (remaining > 0) {
    if (onCenter(g.x, g.y)) {
      if (g.state === VULN.EYES
        && tileOfX(g.x) === CONFIG.doorCol
        && tileOfY(g.y) === CONFIG.houseExitRow) {
        g.state = VULN.ENTERING;
        g.dir = DOWN;
        return;
      }
      chooseDir(sim, g);
    }
    const dx = DX[g.dir];
    const dy = DY[g.dir];
    const d = dx !== 0 ? stepToCenter(g.x, dx) : stepToCenter(g.y, dy);
    const m = d < remaining ? d : remaining;
    g.x = wrapX(g.x + dx * m);
    g.y += dy * m;
    remaining -= m;
  }
}

function moveHouse(sim, g) {
  const laneY = tileCenterY(CONFIG.houseLaneRow);
  g.bob += g.bobDir;
  if (g.bob >= CONFIG.houseBobUnits) {
    g.bob = CONFIG.houseBobUnits;
    g.bobDir = -1;
  } else if (g.bob <= -CONFIG.houseBobUnits) {
    g.bob = -CONFIG.houseBobUnits;
    g.bobDir = 1;
  }
  g.y = laneY + g.bob;
  g.dir = g.bobDir > 0 ? DOWN : UP;
}

/**
 * Getting out of the house, and back into it, is scripted rather than routed.
 *
 * The door is the one tile in the maze nothing is allowed to walk through, so
 * asking the normal movement code to handle it would mean carrying an
 * exception through every wall test. Three straight legs — onto the lane, over
 * to the door, up and out — cost less and land on exact sub-units, because
 * each leg clamps to the distance remaining.
 *
 * The first leg is gated on still being off the door column, and that gate is
 * load-bearing rather than an optimisation. Climbing out moves off the lane
 * row, so an ungated first leg sees "not on the lane" and drags it straight
 * back down: the two legs undo each other and the thing bobs on the doorstep
 * for the rest of the game. Only the vulnerability that starts on the door
 * column ever got out, which is not a subtle bug to play against.
 */
function moveLeaving(sim, g) {
  const laneY = tileCenterY(CONFIG.houseLaneRow);
  const doorX = tileCenterX(CONFIG.doorCol);
  const exitY = tileCenterY(CONFIG.houseExitRow);
  let remaining = vulnSpeed(sim, g);

  while (remaining > 0) {
    if (g.x !== doorX && g.y !== laneY) {
      const gap = laneY > g.y ? laneY - g.y : g.y - laneY;
      const m = gap < remaining ? gap : remaining;
      g.dir = laneY > g.y ? DOWN : UP;
      g.y += laneY > g.y ? m : -m;
      remaining -= m;
    } else if (g.x !== doorX) {
      const gap = doorX > g.x ? doorX - g.x : g.x - doorX;
      const m = gap < remaining ? gap : remaining;
      g.dir = doorX > g.x ? RIGHT : LEFT;
      g.x += doorX > g.x ? m : -m;
      remaining -= m;
    } else if (g.y > exitY) {
      const gap = g.y - exitY;
      const m = gap < remaining ? gap : remaining;
      g.dir = UP;
      g.y -= m;
      remaining -= m;
    } else {
      g.state = VULN.OUT;
      g.dir = LEFT;
      return;
    }
  }
}

function moveEntering(sim, g) {
  const laneY = tileCenterY(CONFIG.houseLaneRow);
  const doorX = tileCenterX(CONFIG.doorCol);
  let remaining = vulnSpeed(sim, g);

  while (remaining > 0) {
    if (g.x !== doorX) {
      const gap = doorX > g.x ? doorX - g.x : g.x - doorX;
      const m = gap < remaining ? gap : remaining;
      g.dir = doorX > g.x ? RIGHT : LEFT;
      g.x += doorX > g.x ? m : -m;
      remaining -= m;
    } else if (g.y < laneY) {
      const gap = laneY - g.y;
      const m = gap < remaining ? gap : remaining;
      g.dir = DOWN;
      g.y += m;
      remaining -= m;
    } else {
      // Patched, filed, and straight back out. Repaired software does not stop
      // being software.
      g.state = VULN.LEAVING;
      g.fright = false;
      return;
    }
  }
}

// --- Collecting -------------------------------------------------------------

function collect(sim) {
  const c = tileOfX(sim.pac.x);
  const r = tileOfY(sim.pac.y);
  const i = r * COLS + c;
  const ch = sim.tiles[i];

  if (ch === PATCH) {
    sim.tiles[i] = FLOOR;
    sim.patchesLeft -= 1;
    sim.patchesEaten += 1;
    sim.totalPatches += 1;
    sim.score += CONFIG.patchPoints;
    sim.houseIdle = 0;
    sim.events.push({ type: 'patch', tick: sim.tick });
    checkBonusSpawn(sim);
  } else if (ch === LOGO) {
    sim.tiles[i] = FLOOR;
    sim.patchesLeft -= 1;
    sim.patchesEaten += 1;
    sim.totalPatches += 1;
    sim.score += CONFIG.logoPoints;
    sim.houseIdle = 0;
    energize(sim);
    sim.events.push({ type: 'logo', tick: sim.tick });
    checkBonusSpawn(sim);
  }

  if (sim.bonusState === 'up' && c === CONFIG.bonusTile[0] && r === CONFIG.bonusTile[1]) {
    const item = bonusFor(sim.level);
    sim.score += item.points;
    sim.bonusState = 'eaten';
    sim.bonusFlash = 120;
    sim.events.push({ type: 'bonus', tick: sim.tick, points: item.points, label: item.label });
  }

  updateElroy(sim);
}

function checkBonusSpawn(sim) {
  if (sim.bonusesShown >= CONFIG.bonusAt.length) return;
  if (sim.patchesEaten < CONFIG.bonusAt[sim.bonusesShown]) return;
  sim.bonusesShown += 1;
  sim.bonusState = 'up';
  sim.bonusTicks = CONFIG.bonusTicks;
  sim.events.push({ type: 'bonusUp', tick: sim.tick });
}

function updateElroy(sim) {
  if (sim.patchesLeft <= CONFIG.elroyAtHarder) sim.elroyStage = 2;
  else if (sim.patchesLeft <= CONFIG.elroyAt) sim.elroyStage = 1;
  else sim.elroyStage = 0;
}

/**
 * A logo goes up. Everything still loose turns patchable, and turns round.
 *
 * The reversal matters more than the colour change: it is the only signal that
 * reads instantly from the far side of the maze, and it is what buys the
 * player the half second needed to decide to give chase.
 */
function energize(sim) {
  sim.frightTicks = frightTicksFor(sim.level);
  sim.frightChain = 0;
  for (let i = 0; i < 4; i += 1) {
    const g = sim.vulns[i];
    if (g.state === VULN.EYES || g.state === VULN.ENTERING) continue;
    g.fright = true;
    if (g.state === VULN.OUT) g.dir = OPPOSITE[g.dir];
  }
}

// --- Phases -----------------------------------------------------------------

function updatePhase(sim) {
  if (sim.frightTicks > 0) {
    // The schedule is suspended, not merely ignored, so a long chase does not
    // silently consume the scatter that was about to give the player a break.
    sim.frightTicks -= 1;
    if (sim.frightTicks === 0) {
      for (let i = 0; i < 4; i += 1) sim.vulns[i].fright = false;
      sim.frightChain = 0;
    }
    return;
  }

  const phases = phasesFor(sim.level);
  if (sim.phaseIndex >= phases.length) return;
  const len = phases[sim.phaseIndex][1];
  if (len === 0) return; // the last phase runs until the board ends

  sim.phaseTicks += 1;
  if (sim.phaseTicks < len) return;

  sim.phaseTicks = 0;
  sim.phaseIndex += 1;
  if (sim.phaseIndex >= phases.length) sim.phaseIndex = phases.length - 1;
  sim.phaseKind = phases[sim.phaseIndex][0];
  for (let i = 0; i < 4; i += 1) {
    const g = sim.vulns[i];
    if (g.state === VULN.OUT) g.dir = OPPOSITE[g.dir];
  }
  sim.events.push({ type: 'phase', tick: sim.tick, kind: sim.phaseKind });
}

function releaseVulns(sim) {
  sim.houseIdle += 1;
  for (let i = 1; i < 4; i += 1) {
    const g = sim.vulns[i];
    if (g.state !== VULN.HOUSE) continue;
    const due = sim.patchesEaten >= CONFIG.releaseAt[i]
      || sim.houseIdle >= CONFIG.releaseIdleTicks;
    if (!due) break; // release in order, so the board fills up predictably
    g.state = VULN.LEAVING;
    sim.houseIdle = 0;
    break;
  }
}

function updateBonus(sim) {
  if (sim.bonusFlash > 0) sim.bonusFlash -= 1;
  if (sim.bonusState !== 'up') return;
  sim.bonusTicks -= 1;
  if (sim.bonusTicks <= 0) sim.bonusState = 'none';
}

// --- Collisions -------------------------------------------------------------

/**
 * Contact is tile equality, not overlapping circles.
 *
 * At these speeds nothing can cross a whole tile in a tick, so two entities
 * cannot swap places without sharing a tile on the way, and a tile comparison
 * survives the tunnel wrap without a special case. It also cannot disagree
 * between two languages, which a distance threshold could.
 */
function resolveContact(sim) {
  const pc = tileOfX(sim.pac.x);
  const pr = tileOfY(sim.pac.y);
  for (let i = 0; i < 4; i += 1) {
    const g = sim.vulns[i];
    if (g.state === VULN.HOUSE || g.state === VULN.EYES || g.state === VULN.ENTERING) continue;
    if (tileOfX(g.x) !== pc || tileOfY(g.y) !== pr) continue;
    if (g.fright) {
      const chain = Math.min(sim.frightChain, CONFIG.vulnPoints.length - 1);
      const points = CONFIG.vulnPoints[chain];
      sim.score += points;
      sim.frightChain += 1;
      sim.vulnsPatched += 1;
      g.fright = false;
      g.state = VULN.EYES;
      sim.freezeTicks = CONFIG.eatFreezeTicks;
      sim.events.push({
        type: 'patched', tick: sim.tick, points, vuln: g.index,
        x: g.x, y: g.y,
      });
      return true;
    }
    die(sim);
    return true;
  }
  return false;
}

function die(sim) {
  sim.state = STATE.DYING;
  sim.deathTicks = CONFIG.deathTicks;
  sim.events.push({ type: 'breach', tick: sim.tick });
}

function afterDeath(sim) {
  sim.lives -= 1;
  if (sim.lives <= 0) {
    finish(sim);
    return;
  }
  resetActors(sim);
  sim.bonusState = 'none';
  sim.state = STATE.READY;
}

function nextLevel(sim) {
  sim.score += CONFIG.levelBonus;
  sim.level += 1;
  loadLevel(sim);
  sim.elroyStage = 0;
  sim.state = STATE.READY;
  sim.events.push({ type: 'level', tick: sim.tick, level: sim.level });
}

function finish(sim) {
  sim.state = STATE.DEAD;
  sim.endTick = sim.tick;
  sim.events.push({ type: 'over', tick: sim.tick, score: sim.score });
}

// --- The tick ---------------------------------------------------------------

/** Advance the world by exactly one fixed timestep. */
export function step(sim) {
  // A finished run has a fixed length. Stepping past the end must change
  // nothing, or two frame rates would disagree on the final tick count purely
  // because they noticed the last life on different frames.
  if (sim.state === STATE.DEAD) return sim;

  sim.pac.prevX = sim.pac.x;
  sim.pac.prevY = sim.pac.y;
  for (let i = 0; i < 4; i += 1) {
    sim.vulns[i].prevX = sim.vulns[i].x;
    sim.vulns[i].prevY = sim.vulns[i].y;
  }

  while (sim.pending.length > 0 && traceTick(sim.pending[0]) <= sim.tick) {
    const code = sim.pending.shift();
    const dir = traceDir(code);
    sim.turns.push(sim.tick * 4 + dir);
    sim.pac.want = dir;
    if (sim.state === STATE.IDLE) {
      sim.state = STATE.READY;
      sim.playStartTick = sim.tick;
      sim.readyTicks = CONFIG.readyTicks;
    }
  }

  if (sim.state === STATE.IDLE) {
    sim.tick += 1;
    return sim;
  }

  if (sim.state === STATE.READY) {
    sim.readyTicks -= 1;
    if (sim.readyTicks <= 0) sim.state = STATE.PLAYING;
    sim.tick += 1;
    return sim;
  }

  if (sim.state === STATE.DYING) {
    sim.deathTicks -= 1;
    if (sim.deathTicks <= 0) afterDeath(sim);
    sim.tick += 1;
    return sim;
  }

  if (sim.state === STATE.CLEAR) {
    sim.clearTicks -= 1;
    if (sim.clearTicks <= 0) nextLevel(sim);
    sim.tick += 1;
    return sim;
  }

  if (sim.freezeTicks > 0) {
    sim.freezeTicks -= 1;
    sim.tick += 1;
    return sim;
  }

  updatePhase(sim);
  updateBonus(sim);
  releaseVulns(sim);

  movePac(sim);
  if (sim.patchesLeft <= 0) {
    sim.state = STATE.CLEAR;
    sim.clearTicks = CONFIG.levelClearTicks;
    sim.events.push({ type: 'cleared', tick: sim.tick, level: sim.level });
    sim.tick += 1;
    return sim;
  }
  if (resolveContact(sim)) {
    sim.tick += 1;
    return sim;
  }

  for (let i = 0; i < 4; i += 1) {
    const g = sim.vulns[i];
    if (g.state === VULN.HOUSE) moveHouse(sim, g);
    else if (g.state === VULN.LEAVING) moveLeaving(sim, g);
    else if (g.state === VULN.ENTERING) moveEntering(sim, g);
    else moveVulnMaze(sim, g);
  }
  resolveContact(sim);

  sim.tick += 1;
  // A hard ceiling both engines share, so a run that is never going to end
  // still ends, and the server always knows how much replay it agreed to.
  if (sim.tick >= CONFIG.maxTicks && sim.state !== STATE.DEAD) finish(sim);
  return sim;
}

/** Milliseconds of actual play, measured in simulation time. */
export function durationMs(sim) {
  if (sim.playStartTick < 0) return 0;
  const end = sim.endTick >= 0 ? sim.endTick : sim.tick;
  return Math.round((end - sim.playStartTick) * CONFIG.stepMs);
}

/**
 * Replay a recorded run.
 *
 * Feeding this the seed and the trace from a submission is how a score gets
 * verified on the server, and how the parity tests check that the JavaScript
 * and the Python still agree.
 */
export function replay(seed, turns, maxTicks = CONFIG.maxTicks + 2) {
  const sim = createSim(seed);
  let next = 0;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    while (next < turns.length && traceTick(turns[next]) <= sim.tick) {
      queueTurn(sim, traceTick(turns[next]), traceDir(turns[next]));
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
    level: sim.level,
    lives: sim.lives,
    patchesLeft: sim.patchesLeft,
    totalPatches: sim.totalPatches,
    pacX: sim.pac.x,
    pacY: sim.pac.y,
    pacDir: sim.pac.dir,
    phaseIndex: sim.phaseIndex,
    phaseKind: sim.phaseKind,
    frightTicks: sim.frightTicks,
    vulnsPatched: sim.vulnsPatched,
    elroyStage: sim.elroyStage,
    endTick: sim.endTick,
    vulns: sim.vulns.map((g) => ({
      x: g.x, y: g.y, dir: g.dir, state: g.state, fright: g.fright ? 1 : 0,
    })),
    turns: sim.turns.slice(),
  };
}

/** Badges the run earned, highest first. Presentation only. */
export function badgesFor(score) {
  return CONFIG.badges.filter((b) => score >= b.at);
}
