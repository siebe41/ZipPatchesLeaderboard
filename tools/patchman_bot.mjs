/**
 * A greedy PatchMan bot, used to generate traces that actually play well.
 *
 * Random input wanders and dies with the logos untouched, which leaves the
 * most delicate parts of the simulation — the frightened window, the only
 * consumer of the generator; the journey home, the only path that follows a
 * distance field; and the level clear — never once exercised. The parity check
 * is only worth what it covers, so it needs runs that reach those.
 *
 * The bot is deliberately simple: breadth-first to whatever it currently wants,
 * take the first step of that path, repeat. It plays well enough to clear
 * levels and badly enough to die, which is exactly the spread wanted.
 *
 *     node tools/patchman_bot.mjs '{"seeds":[1,2,3],"maxTicks":9000}'
 */
import { CONFIG } from '../app/patchman/config.mjs';
import { COLS, ROWS, wrapCol, isWall, isDoor, isHouse, tileOfX, tileOfY }
  from '../app/patchman/maze.mjs';
import { createSim, queueTurn, step, STATE, VULN }
  from '../app/patchman/sim.mjs';

const cols = COLS;
const rows = ROWS;
const DX = [0, -1, 0, 1];
const DY = [-1, 0, 1, 0];

const tileOf = (x, y) => [tileOfX(x), tileOfY(y)];

/** Where the bot is allowed to walk. The house is off limits, as it is to pac. */
function passable(c, r) {
  return !isWall(c, r) && !isDoor(c, r) && !isHouse(c, r);
}

/** First step of a shortest path from `start` to any tile in `goals`. */
function firstStep(start, goals, avoid) {
  const goal = new Set(goals.map(([c, r]) => r * cols + c));
  if (goal.size === 0) return -1;
  const blocked = new Set(avoid.map(([c, r]) => r * cols + c));
  const from = new Int32Array(cols * rows).fill(-1);
  const seen = new Uint8Array(cols * rows);
  const queue = [start[1] * cols + start[0]];
  seen[queue[0]] = 1;
  let head = 0;
  let found = -1;
  while (head < queue.length) {
    const at = queue[head++];
    if (goal.has(at)) { found = at; break; }
    const c = at % cols;
    const r = (at - c) / cols;
    for (let d = 0; d < 4; d += 1) {
      const nc = wrapCol(c + DX[d]);
      const nr = r + DY[d];
      if (nr < 0 || nr >= rows) continue;
      const next = nr * cols + nc;
      if (seen[next] || !passable(nc, nr)) continue;
      if (blocked.has(next) && !goal.has(next)) continue;
      seen[next] = 1;
      from[next] = at;
      queue.push(next);
    }
  }
  if (found < 0) return -1;
  let at = found;
  const origin = start[1] * cols + start[0];
  if (at === origin) return -1;
  while (from[at] !== origin) {
    at = from[at];
    if (at < 0) return -1;
  }
  const c = at % cols;
  const r = (at - c) / cols;
  const sc = start[0];
  const sr = start[1];
  if (r === sr - 1) return 0;
  if (r === sr + 1) return 2;
  // Columns wrap, so compare the wrapped neighbour rather than the raw number.
  if (wrapCol(sc - 1) === c) return 1;
  if (wrapCol(sc + 1) === c) return 3;
  return -1;
}

function tilesWith(sim, kinds) {
  const out = [];
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      if (kinds.includes(sim.tiles[r * cols + c])) out.push([c, r]);
    }
  }
  return out;
}

/**
 * What the bot wants right now, in priority order.
 *
 * The ordering matters more than it looks. A bot that eats a logo the moment
 * it sees one spends the whole frightened window alone, because the
 * vulnerabilities are still in the house, and the run never exercises the
 * chase, the eyes journey home, or the doubling chain. So a logo is only worth
 * taking when there is something nearby to catch with it.
 */
function chooseGoals(sim, pacTile, greed) {
  const chasing = sim.vulns
    .filter((g) => g.fright && g.state === VULN.OUT)
    .map((g) => tileOf(g.x, g.y));
  if (chasing.length) return { goals: chasing, avoid: [] };

  const danger = [];
  let nearestHunter = 99;
  for (const g of sim.vulns) {
    if (g.fright || g.state !== VULN.OUT) continue;
    const [gc, gr] = tileOf(g.x, g.y);
    const dist = Math.abs(gc - pacTile[0]) + Math.abs(gr - pacTile[1]);
    nearestHunter = Math.min(nearestHunter, dist);
    if (dist <= 5) danger.push([gc, gr]);
  }

  const logos = tilesWith(sim, ['o']);
  const bait = greed ? 12 : 7;
  if (logos.length && nearestHunter <= bait) return { goals: logos, avoid: [] };
  const patches = tilesWith(sim, ['.']);
  if (patches.length) return { goals: patches, avoid: danger };
  return { goals: logos, avoid: danger };
}

/**
 * The shortest gap the bot will leave between two inputs, in ticks.
 *
 * Without this the bot re-plans every tick and, whenever two routes tie, flips
 * between them on consecutive ticks. That produces hundreds of inputs a tick
 * apart, which is not a hand and which the server correctly refuses to count.
 * The gap is jittered deterministically so the bot does not simply trade one
 * machine signature for another, and stays a function of the seed alone.
 */
const MIN_INPUT_GAP = 7;
const INPUT_GAP_SPREAD = 7;

function inputGap(seed, tick) {
  const h = Math.imul(seed ^ tick, 2654435761) >>> 0;
  return MIN_INPUT_GAP + (h % INPUT_GAP_SPREAD);
}

export function botTrace(seed, maxTicks, greed) {
  const sim = createSim(seed);
  const turns = [];
  let last = -1;
  let lastTick = -1000;
  while (sim.state !== STATE.DEAD && sim.tick < maxTicks) {
    const pacTile = tileOf(sim.pac.x, sim.pac.y);
    const { goals, avoid } = chooseGoals(sim, pacTile, greed);
    let dir = firstStep(pacTile, goals, avoid);
    if (dir < 0) dir = firstStep(pacTile, goals, []);
    if (dir >= 0 && dir !== last && sim.tick - lastTick >= inputGap(seed, sim.tick)) {
      queueTurn(sim, sim.tick, dir);
      turns.push(sim.tick * 4 + dir);
      last = dir;
      lastTick = sim.tick;
    }
    step(sim);
  }
  return turns;
}

if (process.argv[2]) {
  const req = JSON.parse(process.argv[2]);
  const max = req.maxTicks || CONFIG.maxTicks;
  const out = req.seeds.map((seed, i) => botTrace(seed, max, i % 2 === 0));
  process.stdout.write(JSON.stringify(out));
}
