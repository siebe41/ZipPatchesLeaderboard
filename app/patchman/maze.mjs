/**
 * The board: what is wall, what carries a patch, and how far anything is from
 * the door.
 *
 * Everything here is derived from the maze literal in config.mjs once, at
 * module load, and never changes afterwards. A run mutates its own copy of the
 * patches; it never touches anything in this file.
 */
import { CONFIG } from './config.mjs';

export const WALL = '#';
export const PATCH = '.';
export const LOGO = 'o';
export const DOOR = '-';
export const FLOOR = ' ';

export const COLS = CONFIG.cols;
export const ROWS = CONFIG.rows;
export const CELL = CONFIG.cell;
export const WORLD_W = COLS * CELL;

// Up, left, down, right. The order is the tie-break order for a vulnerability
// choosing between two equally good turns, so it is part of the rules, not a
// detail of how the array happens to be written.
export const UP = 0;
export const LEFT = 1;
export const DOWN = 2;
export const RIGHT = 3;
export const NONE = -1;

export const DX = [0, -1, 0, 1];
export const DY = [-1, 0, 1, 0];
export const OPPOSITE = [DOWN, RIGHT, UP, LEFT];

const GRID = CONFIG.maze.map((row) => row.split(''));

/** Column wrapped into the board, which is what makes the tunnel a tunnel. */
export function wrapCol(c) {
  return ((c % COLS) + COLS) % COLS;
}

export function tileChar(c, r) {
  if (r < 0 || r >= ROWS) return WALL;
  return GRID[r][wrapCol(c)];
}

export function isWall(c, r) {
  return tileChar(c, r) === WALL;
}

export function isDoor(c, r) {
  return tileChar(c, r) === DOOR;
}

/** Tiles inside the house, which nothing walks into except through the door. */
export function isHouse(c, r) {
  const row = r;
  const col = wrapCol(c);
  return row >= CONFIG.houseLaneRow && row <= CONFIG.houseLaneRow + 1
    && col >= 11 && col <= 15;
}

/** The tiles that count as the tunnel, where a vulnerability slows down. */
export function isTunnel(c, r) {
  if (r !== 14) return false;
  const col = wrapCol(c);
  return col <= 4 || col >= COLS - 5;
}

/**
 * The starting patches, as a flat array of tile characters.
 *
 * A run owns this array and eats out of it, so a level reset is a fresh copy
 * rather than a mutation of anything shared.
 */
export function freshPatches() {
  const out = new Array(COLS * ROWS);
  let patches = 0;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const ch = GRID[r][c];
      const keep = ch === PATCH || ch === LOGO;
      out[r * COLS + c] = keep ? ch : FLOOR;
      if (keep) patches += 1;
    }
  }
  return { tiles: out, count: patches };
}

export const TOTAL_PATCHES = freshPatches().count;

/**
 * Steps from every tile back to the corridor above the door.
 *
 * The chase rules are greedy: a vulnerability picks whichever turn puts it
 * closest to its target as the crow flies, which is what gives each of them a
 * personality but is not guaranteed to arrive anywhere. That is fine while it
 * is hunting, and useless when it has been patched and has to actually get
 * home. So going home uses a real breadth-first distance field instead, which
 * is exact, cannot loop, and is identical in any language because it is
 * nothing but integer counting.
 */
function buildHomeDistance() {
  const dist = new Array(COLS * ROWS).fill(-1);
  const goal = CONFIG.doorCol + CONFIG.houseExitRow * COLS;
  dist[goal] = 0;
  const queue = [goal];
  let head = 0;
  while (head < queue.length) {
    const at = queue[head];
    head += 1;
    const c = at % COLS;
    const r = (at - c) / COLS;
    for (let d = 0; d < 4; d += 1) {
      const nr = r + DY[d];
      if (nr < 0 || nr >= ROWS) continue;
      const nc = wrapCol(c + DX[d]);
      const idx = nr * COLS + nc;
      if (dist[idx] >= 0) continue;
      if (isWall(nc, nr) || isDoor(nc, nr) || isHouse(nc, nr)) continue;
      dist[idx] = dist[at] + 1;
      queue.push(idx);
    }
  }
  return dist;
}

export const HOME_DISTANCE = buildHomeDistance();

export function homeDistance(c, r) {
  if (r < 0 || r >= ROWS) return -1;
  return HOME_DISTANCE[r * COLS + wrapCol(c)];
}

/** Sub-unit position of the centre of a tile. */
export function tileCenterX(c) {
  return c * CELL + CELL / 2;
}

export function tileCenterY(r) {
  return r * CELL + CELL / 2;
}

export function tileOfX(x) {
  return Math.floor(x / CELL);
}

export function tileOfY(y) {
  return Math.floor(y / CELL);
}

export function onCenter(x, y) {
  return x % CELL === CELL / 2 && y % CELL === CELL / 2;
}

/**
 * Sub-units from here to the next tile centre in the direction of travel.
 *
 * Always between 1 and CELL, never 0: an entity standing on a centre is a full
 * tile away from the next one, not no distance at all. Moving in these hops
 * rather than one sub-unit at a time is what keeps a replay cheap enough for
 * the server to do on every submission.
 */
export function stepToCenter(p, delta) {
  const off = p % CELL;
  if (delta > 0) {
    const d = (CELL / 2 - off + CELL) % CELL;
    return d === 0 ? CELL : d;
  }
  const d = (off - CELL / 2 + CELL) % CELL;
  return d === 0 ? CELL : d;
}
