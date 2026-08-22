/**
 * Drawing. Reads the simulation, never writes to it.
 *
 * Everything here is vector work against the 2D context. There is no sprite
 * sheet, which is deliberate twice over: a maze chase needs one static board
 * and a handful of shapes rather than hundreds of frames, and the only piece of
 * real artwork in the game is the Patch My PC logo, which stays sharp at any
 * size precisely because nothing else is pinned to a pixel grid.
 *
 * The board never changes shape, so it is drawn once into an offscreen canvas
 * at device resolution and blitted every frame. That turns roughly nine hundred
 * path operations a frame into one.
 *
 * Positions arriving from the simulation are integers in sub-units. They are
 * interpolated between the previous and current tick here, so a 144 Hz display
 * gets smooth motion out of a 120 Hz simulation without either one influencing
 * the other.
 */
import { CONFIG } from './config.mjs';
import {
  COLS, ROWS, CELL, WORLD_W,
  isWall, isDoor, isHouse,
} from './maze.mjs';
import { STATE, VULN } from './sim.mjs';

const T = CONFIG.tile;
const TOP = CONFIG.mazeTop;
const W = CONFIG.width;
const H = CONFIG.height;
const MAZE_H = ROWS * T;
const FOOT = TOP + MAZE_H;          // top of the bottom strip

const COLORS = {
  void: '#070b16',
  grid: 'rgba(78, 204, 163, 0.045)',
  wallEdge: '#2f7fd0',
  wallFill: '#0d1b33',
  wallVia: 'rgba(120, 190, 255, 0.20)',
  door: '#7ac143',
  patch: '#ffcf5c',
  text: '#eeeeee',
  dim: '#9aa4b2',
  accent: '#4ecca3',
  accent2: '#36a2eb',
  warn: '#e94560',
  gold: '#ffd23f',
  panel: 'rgba(7, 11, 22, 0.93)',
  pac: '#7ac143',
  pacCore: '#c3ec8a',
};

const MUTE_BOX = { x: W - 22, y: FOOT + 4, w: 16, h: 16 };
const FONT = 'Consolas, "SF Mono", "Roboto Mono", ui-monospace, monospace';

/**
 * Mix a six digit hex colour towards white (positive) or black (negative).
 *
 * This exists for the beetle legs. A real one has black legs, and black legs on
 * a near black maze is a beetle that reads as a floating disc, so they are
 * drawn in a pale wash of the shell colour instead.
 */
function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const to = amount > 0 ? 255 : 0;
  const k = Math.min(1, Math.abs(amount));
  const ch = (s) => {
    const v = (n >> s) & 255;
    return Math.round(v + (to - v) * k);
  };
  return `rgb(${ch(16)}, ${ch(8)}, ${ch(0)})`;
}

/** Load an image, resolving to null rather than throwing if it is missing. */
export function loadImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

/**
 * Interpolate a sub-unit coordinate, unless the entity just came through the
 * tunnel. Half a world apart in one tick means it wrapped, and averaging the
 * two ends would fling it across the screen.
 */
function lerpX(prev, cur, alpha) {
  if (Math.abs(cur - prev) > WORLD_W / 2) return cur;
  return prev + (cur - prev) * alpha;
}

function lerp(prev, cur, alpha) {
  return prev + (cur - prev) * alpha;
}

const DIR_ANGLE = [-Math.PI / 2, Math.PI, Math.PI / 2, 0];

// --------------------------------------------------------------------------

export function createRenderer(canvas, logo) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let scale = 1;
  let board = null;          // offscreen canvas holding the static maze

  /**
   * Size the backing store to whole device pixels.
   *
   * Unlike the pixel-art game next door there is no sprite grid to keep
   * aligned, so the scale does not have to be an integer; it only has to put
   * one backing pixel on one device pixel, which is what keeps hairlines from
   * going soft.
   */
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const parent = canvas.parentElement;
    const availW = parent.clientWidth;
    const availH = parent.clientHeight;
    const fit = Math.max(0.25, Math.min(availW / W, availH / H));
    scale = Math.max(1, fit * dpr);
    canvas.width = Math.round(W * scale);
    canvas.height = Math.round(H * scale);
    canvas.style.width = Math.round(W * fit) + 'px';
    canvas.style.height = Math.round(H * fit) + 'px';
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = true;
    buildBoard();
  }

  function toLogical(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left) * (W / rect.width),
      y: (clientY - rect.top) * (H / rect.height),
    };
  }

  function hitsMute(clientX, clientY) {
    const p = toLogical(clientX, clientY);
    return p.x >= MUTE_BOX.x - 8 && p.x <= MUTE_BOX.x + MUTE_BOX.w + 8
      && p.y >= MUTE_BOX.y - 8 && p.y <= MUTE_BOX.y + MUTE_BOX.h + 8;
  }

  // ------------------------------------------------------------------------
  // The board, drawn once
  // ------------------------------------------------------------------------

  /**
   * Fill the union of every wall tile, inset by `pad` and with rounded outer
   * corners.
   *
   * Each tile contributes a rounded rectangle, and each pair of adjacent tiles
   * contributes a bridge across the seam between them. The bridges swallow the
   * rounding wherever two wall tiles meet, so a long block reads as one shape
   * with rounded ends while a lone tile stays a rounded square. Doing it this
   * way avoids tracing the outline of the wall region, which is a far longer
   * piece of code for a result nobody could tell apart at sixteen pixels.
   *
   * Drawing it twice, a wide bright pass under a narrower dark one, is what
   * produces the trace-and-solder-mask look without a second path.
   */
  function fillWalls(g, pad, color) {
    g.fillStyle = color;
    const r = Math.max(0, 5 - pad);
    for (let row = 0; row < ROWS; row += 1) {
      for (let col = 0; col < COLS; col += 1) {
        if (!isWall(col, row)) continue;
        const x = col * T;
        const y = row * T;
        roundRect(g, x + pad, y + pad, T - pad * 2, T - pad * 2, r);
        g.fill();
        if (col + 1 < COLS && isWall(col + 1, row)) {
          g.fillRect(x + T - r - pad, y + pad, (r + pad) * 2, T - pad * 2);
        }
        if (row + 1 < ROWS && isWall(col, row + 1)) {
          g.fillRect(x + pad, y + T - r - pad, T - pad * 2, (r + pad) * 2);
        }
      }
    }
  }

  /** Vias and traces inside the solid blocks, so the walls read as board. */
  function detailWalls(g) {
    g.fillStyle = COLORS.wallVia;
    for (let row = 1; row < ROWS - 1; row += 1) {
      for (let col = 1; col < COLS - 1; col += 1) {
        if (!isWall(col, row)) continue;
        // Only tiles buried inside a block, so a via never lands on an edge.
        if (!isWall(col - 1, row) || !isWall(col + 1, row)
          || !isWall(col, row - 1) || !isWall(col, row + 1)) continue;
        if ((col + row) % 2) continue;
        g.fillRect(col * T + T / 2 - 1, row * T + T / 2 - 1, 2, 2);
      }
    }
  }

  function drawDoor(g) {
    for (let row = 0; row < ROWS; row += 1) {
      for (let col = 0; col < COLS; col += 1) {
        if (!isDoor(col, row)) continue;
        g.fillStyle = COLORS.door;
        g.fillRect(col * T + 1, row * T + T / 2 - 1.5, T - 2, 3);
      }
    }
  }

  function buildBoard() {
    const c = document.createElement('canvas');
    c.width = Math.round(W * scale);
    c.height = Math.round(MAZE_H * scale);
    const g = c.getContext('2d');
    g.setTransform(scale, 0, 0, scale, 0, 0);

    g.fillStyle = COLORS.void;
    g.fillRect(0, 0, W, MAZE_H);

    // A faint substrate grid, one line per tile.
    g.strokeStyle = COLORS.grid;
    g.lineWidth = 1;
    g.beginPath();
    for (let col = 0; col <= COLS; col += 1) {
      g.moveTo(col * T + 0.5, 0);
      g.lineTo(col * T + 0.5, MAZE_H);
    }
    for (let row = 0; row <= ROWS; row += 1) {
      g.moveTo(0, row * T + 0.5);
      g.lineTo(W, row * T + 0.5);
    }
    g.stroke();

    // The house floor, so the cage reads as a component socket.
    g.fillStyle = 'rgba(54, 162, 235, 0.07)';
    for (let row = 0; row < ROWS; row += 1) {
      for (let col = 0; col < COLS; col += 1) {
        if (isHouse(col, row)) g.fillRect(col * T, row * T, T, T);
      }
    }

    fillWalls(g, 1, COLORS.wallEdge);
    fillWalls(g, 2.5, COLORS.wallFill);
    detailWalls(g);
    drawDoor(g);

    board = c;
  }

  // ------------------------------------------------------------------------
  // Entities
  // ------------------------------------------------------------------------

  function px(x) { return x / CELL * T; }
  function py(y) { return TOP + (y / CELL) * T; }

  function drawPatches(sim, nowMs) {
    const tiles = sim.tiles;
    ctx.fillStyle = COLORS.patch;
    for (let row = 0; row < ROWS; row += 1) {
      for (let col = 0; col < COLS; col += 1) {
        if (tiles[row * COLS + col] !== '.') continue;
        const x = col * T + T / 2;
        const y = TOP + row * T + T / 2;
        ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
      }
    }

    // The logos breathe, which is what stops four static dots on a board full
    // of movement from looking like scenery.
    const pulse = 0.5 + 0.5 * Math.sin(nowMs / 1000 * Math.PI * 1.4);
    const size = 13 + pulse * 4;
    for (let row = 0; row < ROWS; row += 1) {
      for (let col = 0; col < COLS; col += 1) {
        if (tiles[row * COLS + col] !== 'o') continue;
        const x = col * T + T / 2;
        const y = TOP + row * T + T / 2;
        ctx.save();
        ctx.shadowColor = 'rgba(122, 193, 67, 0.85)';
        ctx.shadowBlur = 6 + pulse * 8;
        if (logo) {
          ctx.drawImage(logo, x - size / 2, y - size / 2, size, size);
        } else {
          ctx.fillStyle = COLORS.door;
          ctx.beginPath();
          ctx.arc(x, y, size / 2, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }
    }
  }

  /**
   * PatchMan: a disc of Patch My PC green with a chomping wedge taken out of
   * it. The wedge is what carries the direction, so it is driven by the
   * simulation's facing rather than by the interpolated position, which would
   * lag it by a frame.
   */
  function drawPac(sim, alpha, nowMs, dying) {
    const p = sim.pac;
    const x = px(lerpX(p.prevX, p.x, alpha));
    const y = py(lerp(p.prevY, p.y, alpha));
    const r = T * 0.42;

    let open;
    if (dying) {
      // The death animation is the mouth opening until nothing is left.
      const t = 1 - Math.max(0, sim.deathTicks) / CONFIG.deathTicks;
      open = Math.min(1, t * 1.15);
    } else if (sim.state === STATE.PLAYING) {
      const frame = Math.floor(nowMs / CONFIG.chompFrameMs) % (CONFIG.chompFrames * 2);
      const f = frame < CONFIG.chompFrames ? frame : CONFIG.chompFrames * 2 - frame;
      open = (f / CONFIG.chompFrames) * 0.55;
    } else {
      open = 0.18;
    }
    if (open >= 0.999) return;   // fully open is gone

    const half = open * Math.PI * 0.55;
    const face = DIR_ANGLE[p.dir];

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(face);
    const grad = ctx.createRadialGradient(-r * 0.3, -r * 0.3, r * 0.15, 0, 0, r);
    grad.addColorStop(0, COLORS.pacCore);
    grad.addColorStop(1, COLORS.pac);
    ctx.fillStyle = grad;
    ctx.shadowColor = 'rgba(122, 193, 67, 0.7)';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, r, half, Math.PI * 2 - half);
    ctx.closePath();
    ctx.fill();
    ctx.shadowBlur = 0;

    // An eye, and a seam across the disc, so it reads as hardware rather than
    // as a plain circle.
    ctx.fillStyle = 'rgba(9, 26, 12, 0.85)';
    ctx.beginPath();
    ctx.arc(r * 0.1, -r * 0.45, r * 0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Each vulnerability gets its own shell markings, so the four stay apart at a
  // glance for anyone who cannot rely on the colour to tell them apart. Points
  // are fractions of the body radius, in a local space where the bug faces up.
  const SHELL_MARKS = [
    [[-0.34, -0.10, 0.20], [0.34, -0.10, 0.20], [0, 0.34, 0.16]],   // RCE
    [[-0.30, -0.24, 0.14], [0.30, -0.24, 0.14],
      [-0.30, 0.26, 0.14], [0.30, 0.26, 0.14]],                     // XSS
    [[0, -0.22, 0.17], [-0.32, 0.20, 0.17], [0.32, 0.20, 0.17]],    // SQLI
    [[-0.33, 0.02, 0.15], [0.33, 0.02, 0.15]],                      // 0DAY
  ];

  // Attach point, knee and tip of one leg, as fractions of the body radius in a
  // local space where the bug faces up. The front pair rakes forward and the
  // back pair rakes back, which is the stance a real one stands in and doubles
  // as a second cue for which way this one is heading.
  const LEGS = [
    [0.50, -0.58, 1.04, -0.82, 1.44, -1.20],
    [0.56, 0.00, 1.12, 0.02, 1.56, 0.16],
    [0.50, 0.58, 1.04, 0.68, 1.40, 1.16],
  ];

  /**
   * A beetle, drawn facing up in local space and rotated into its direction of
   * travel so the head and the legs lead the way.
   *
   * Everything is vectors for the same reason the rest of the board is: an
   * asset would have to be authored once per colour per state, and this has
   * four colours, a frightened palette, and a flash frame.
   */
  function drawBeetle(g, r, wobble, nowMs, opts) {
    const bodyR = r * 0.78;

    // Six legs in a tripod gait: the front and back on one side swing with the
    // middle leg on the other, which is how a real one walks and is why this
    // reads as scuttling rather than as a shape being nudged. They also have to
    // poke well past the shell, because a circle with a face on it is a ball.
    g.strokeStyle = opts.leg;
    g.lineWidth = Math.max(1.1, r * 0.15);
    g.lineCap = 'round';
    g.lineJoin = 'round';
    for (const side of [-1, 1]) {
      for (let i = 0; i < 3; i += 1) {
        const swing = ((i + (side < 0 ? 1 : 0)) % 2 === wobble) ? 1 : -1;
        const [ax, ay, kx, ky, tx, ty] = LEGS[i];
        g.beginPath();
        g.moveTo(side * ax * bodyR, ay * bodyR);
        g.lineTo(side * kx * bodyR, (ky + swing * 0.10) * bodyR);
        g.lineTo(side * tx * bodyR, (ty + swing * 0.20) * bodyR);
        g.stroke();
      }
    }

    // Antennae, twitching as it moves.
    const twitch = Math.sin(nowMs / 110 + opts.phase) * r * 0.1;
    g.lineWidth = Math.max(1, r * 0.11);
    for (const side of [-1, 1]) {
      g.beginPath();
      g.moveTo(side * bodyR * 0.18, -bodyR * 1.16);
      g.quadraticCurveTo(
        side * bodyR * 0.68, -bodyR * 1.52,
        side * (bodyR * 0.82 + twitch), -bodyR * 1.70,
      );
      g.stroke();
    }

    // Head. It sits proud of the shell rather than tucked under it, so the
    // silhouette has a nose and you can tell at a glance where it is pointed.
    g.fillStyle = opts.head;
    g.beginPath();
    g.ellipse(0, -bodyR * 1.02, bodyR * 0.46, bodyR * 0.32, 0, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = opts.leg;
    g.lineWidth = Math.max(1, r * 0.08);
    g.stroke();

    // Shell.
    g.save();
    g.shadowColor = opts.fill;
    g.shadowBlur = 7;
    g.fillStyle = opts.fill;
    g.beginPath();
    g.ellipse(0, 0, bodyR * 0.92, bodyR, 0, 0, Math.PI * 2);
    g.fill();
    g.restore();

    g.strokeStyle = opts.edge;
    g.lineWidth = 1;
    g.beginPath();
    g.ellipse(0, 0, bodyR * 0.92, bodyR, 0, 0, Math.PI * 2);
    g.stroke();

    // The seam down the elytra, and the pronotum band behind the head.
    g.strokeStyle = opts.mark;
    g.lineWidth = Math.max(1, r * 0.1);
    g.beginPath();
    g.moveTo(0, -bodyR * 0.62);
    g.lineTo(0, bodyR * 0.9);
    g.stroke();
    g.fillStyle = opts.mark;
    g.beginPath();
    g.ellipse(0, -bodyR * 0.66, bodyR * 0.62, bodyR * 0.22, 0, 0, Math.PI * 2);
    g.fill();

    for (const [mx, my, mr] of opts.marks) {
      g.beginPath();
      g.arc(mx * bodyR, my * bodyR, mr * bodyR, 0, Math.PI * 2);
      g.fill();
    }

    // A scanline across the shell, so a flat colour still reads as rendered.
    g.save();
    g.beginPath();
    g.ellipse(0, 0, bodyR * 0.92, bodyR, 0, 0, Math.PI * 2);
    g.clip();
    g.fillStyle = 'rgba(255, 255, 255, 0.14)';
    g.fillRect(-bodyR, -bodyR + ((nowMs / 9 + opts.phase * 37) % (bodyR * 2)), bodyR * 2, 1);
    g.restore();
  }

  /** Eyes on the head, looking where it is going. Local space, facing up. */
  function beetleEyes(g, r, color) {
    const bodyR = r * 0.78;
    for (const side of [-1, 1]) {
      g.fillStyle = '#f4f8ff';
      g.beginPath();
      g.arc(side * bodyR * 0.22, -bodyR * 1.02, bodyR * 0.16, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = color;
      g.beginPath();
      g.arc(side * bodyR * 0.22, -bodyR * 1.07, bodyR * 0.09, 0, Math.PI * 2);
      g.fill();
    }
  }

  /**
   * Crossed-out eyes, which is what a patchable one wears.
   *
   * This is the cue that does not depend on the colour: green says patchable to
   * anyone who can see the green, and the crosses say it to everyone else.
   */
  function beetleCrosses(g, r, color) {
    const bodyR = r * 0.78;
    g.strokeStyle = color;
    g.lineWidth = Math.max(1, r * 0.12);
    g.lineCap = 'round';
    for (const side of [-1, 1]) {
      const cx = side * bodyR * 0.22;
      const cy = -bodyR * 1.02;
      const s = bodyR * 0.16;
      g.beginPath();
      g.moveTo(cx - s, cy - s); g.lineTo(cx + s, cy + s);
      g.moveTo(cx + s, cy - s); g.lineTo(cx - s, cy + s);
      g.stroke();
    }
  }

  function vulnEyes(g, x, y, r, dir, color) {
    const dx = [0, -1, 0, 1][dir] * r * 0.22;
    const dy = [-1, 0, 1, 0][dir] * r * 0.22;
    for (const side of [-1, 1]) {
      const ex = x + side * r * 0.36;
      const ey = y - r * 0.18;
      g.fillStyle = '#f4f8ff';
      g.beginPath();
      g.ellipse(ex, ey, r * 0.28, r * 0.34, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = color;
      g.beginPath();
      g.arc(ex + dx, ey + dy, r * 0.15, 0, Math.PI * 2);
      g.fill();
    }
  }

  function drawVulns(sim, alpha, nowMs) {
    const wobble = Math.floor(nowMs / 140) % 2;
    const flashing = sim.frightTicks > 0
      && sim.frightTicks <= CONFIG.frightenedFlashTicks
      && Math.floor(sim.frightTicks / 24) % 2 === 0;

    for (let i = 0; i < 4; i += 1) {
      const g = sim.vulns[i];
      const spec = CONFIG.vulns[i];
      const x = px(lerpX(g.prevX, g.x, alpha));
      const y = py(lerp(g.prevY, g.y, alpha));
      const r = T * 0.44;
      const eyesOnly = g.state === VULN.EYES || g.state === VULN.ENTERING;

      if (eyesOnly) {
        // Patched, and only the eyes are still walking home.
        vulnEyes(ctx, x, y, r, g.dir, COLORS.accent2);
        continue;
      }

      let fill = spec.color;
      let edge = 'rgba(255, 255, 255, 0.28)';
      let dark = 'rgba(9, 12, 26, 0.85)';
      if (g.fright) {
        fill = flashing ? CONFIG.frightFlashColor : CONFIG.frightColor;
        edge = flashing ? '#ffffff' : '#d4f2a8';
        dark = flashing ? '#1d3f0c' : 'rgba(9, 26, 12, 0.85)';
      }

      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(DIR_ANGLE[g.dir] + Math.PI / 2);
      drawBeetle(ctx, r, wobble, nowMs, {
        fill,
        edge,
        head: dark,
        leg: shade(fill, 0.42),
        mark: dark,
        marks: SHELL_MARKS[i],
        phase: i,
      });
      if (g.fright) beetleCrosses(ctx, r, dark);
      else beetleEyes(ctx, r, '#131a2c');
      ctx.restore();
    }
  }

  function drawBonus(sim, nowMs) {
    if (sim.bonusState !== 'up') return;
    const item = CONFIG.bonuses[Math.min(sim.level - 1, CONFIG.bonuses.length - 1)];
    const x = CONFIG.bonusTile[0] * T + T / 2;
    const y = TOP + CONFIG.bonusTile[1] * T + T / 2;
    const bob = Math.sin(nowMs / 240) * 1.5;

    ctx.save();
    ctx.translate(x, y + bob);
    // A shipping box: a chip outline with a label plate under it.
    ctx.shadowColor = COLORS.gold;
    ctx.shadowBlur = 8;
    ctx.fillStyle = '#1a2b4d';
    roundRect(ctx, -8, -8, 16, 16, 3);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = COLORS.gold;
    ctx.lineWidth = 1.2;
    roundRect(ctx, -8, -8, 16, 16, 3);
    ctx.stroke();
    ctx.fillStyle = COLORS.gold;
    for (let i = 0; i < 3; i += 1) {
      ctx.fillRect(-10, -4 + i * 4, 2, 2);
      ctx.fillRect(8, -4 + i * 4, 2, 2);
    }
    ctx.fillStyle = COLORS.gold;
    ctx.font = '700 7px ' + FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('PKG', 0, 0.5);
    ctx.restore();

    ctx.fillStyle = COLORS.dim;
    ctx.font = '700 7px ' + FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(item.label, x, y + 11);
  }

  /** Points that float up where they were earned. */
  function drawPopups(popups, nowMs) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const p of popups) {
      const age = (nowMs - p.at) / p.life;
      if (age >= 1) continue;
      ctx.globalAlpha = Math.max(0, 1 - age * age);
      ctx.fillStyle = p.color;
      ctx.font = '700 10px ' + FONT;
      ctx.fillText(p.text, p.x, p.y - age * 14);
    }
    ctx.globalAlpha = 1;
  }

  // ------------------------------------------------------------------------
  // Furniture
  // ------------------------------------------------------------------------

  function drawPacIcon(x, y, r) {
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = COLORS.pac;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, r, Math.PI * 0.16, Math.PI * 1.84);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function drawHud(view) {
    const sim = view.sim;
    ctx.fillStyle = 'rgba(9, 14, 28, 0.9)';
    ctx.fillRect(0, 0, W, TOP);
    ctx.fillStyle = 'rgba(78, 204, 163, 0.25)';
    ctx.fillRect(0, TOP - 1, W, 1);

    ctx.textBaseline = 'top';
    ctx.font = '700 8px ' + FONT;
    ctx.fillStyle = COLORS.dim;
    ctx.textAlign = 'left';
    ctx.fillText('SCORE', 10, 7);
    ctx.textAlign = 'right';
    ctx.fillText('BEST', W - 10, 7);
    ctx.textAlign = 'center';
    ctx.fillText('LEVEL', W / 2, 7);

    ctx.font = '700 15px ' + FONT;
    ctx.fillStyle = COLORS.text;
    ctx.textAlign = 'left';
    ctx.fillText(String(view.score), 10, 18);
    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.gold;
    ctx.fillText(String(view.best), W - 10, 18);
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.accent2;
    ctx.fillText(sim ? String(sim.level) : '1', W / 2, 18);
  }

  function drawFooter(view) {
    const sim = view.sim;
    ctx.fillStyle = 'rgba(9, 14, 28, 0.9)';
    ctx.fillRect(0, FOOT, W, H - FOOT);
    ctx.fillStyle = 'rgba(78, 204, 163, 0.25)';
    ctx.fillRect(0, FOOT, W, 1);

    // Lives, as spare copies of PatchMan waiting to be deployed.
    const lives = sim ? Math.max(0, sim.lives - (sim.state === STATE.DEAD ? 0 : 1)) : 0;
    for (let i = 0; i < lives; i += 1) {
      drawPacIcon(14 + i * 16, FOOT + 12, 5.5);
    }

    // The frightened window, as a draining bar. It is the only timer that
    // decides anything, so it is the only one shown.
    if (sim && sim.frightTicks > 0) {
      const full = CONFIG.frightenedTicks[
        Math.min(sim.level - 1, CONFIG.frightenedTicks.length - 1)];
      const frac = Math.max(0, Math.min(1, sim.frightTicks / full));
      const bw = 150;
      const bx = (W - bw) / 2;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.10)';
      ctx.fillRect(bx, FOOT + 9, bw, 6);
      ctx.fillStyle = sim.frightTicks <= CONFIG.frightenedFlashTicks
        ? COLORS.warn : COLORS.door;
      ctx.fillRect(bx, FOOT + 9, bw * frac, 6);
      ctx.fillStyle = COLORS.dim;
      ctx.font = '700 7px ' + FONT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText('PATCH WINDOW', W / 2, FOOT + 1);
    } else if (sim && sim.vulnsPatched > 0) {
      ctx.fillStyle = COLORS.dim;
      ctx.font = '700 8px ' + FONT;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(sim.vulnsPatched + ' PATCHED', W / 2, FOOT + 12);
    }

    // Mute, drawn as a speaker with or without a bar through it.
    const m = MUTE_BOX;
    ctx.fillStyle = view.muted ? COLORS.dim : COLORS.accent;
    ctx.beginPath();
    ctx.moveTo(m.x + 2, m.y + 6);
    ctx.lineTo(m.x + 5, m.y + 6);
    ctx.lineTo(m.x + 9, m.y + 2);
    ctx.lineTo(m.x + 9, m.y + 14);
    ctx.lineTo(m.x + 5, m.y + 10);
    ctx.lineTo(m.x + 2, m.y + 10);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = view.muted ? COLORS.warn : COLORS.accent;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    if (view.muted) {
      ctx.moveTo(m.x + 11, m.y + 4);
      ctx.lineTo(m.x + 16, m.y + 12);
    } else {
      ctx.arc(m.x + 9, m.y + 8, 4, -0.9, 0.9);
      ctx.moveTo(m.x + 16, m.y + 4.5);
      ctx.arc(m.x + 9, m.y + 8, 6.5, -0.8, 0.8);
    }
    ctx.stroke();
  }

  function panel(x, y, w, h, accent) {
    ctx.fillStyle = COLORS.panel;
    roundRect(ctx, x, y, w, h, 6);
    ctx.fill();
    ctx.strokeStyle = accent || COLORS.accent;
    ctx.lineWidth = 1;
    roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, 6);
    ctx.stroke();
  }

  /** Centred lines of text on a plate, used for every overlay. */
  function messagePanel(lines, accent) {
    if (!lines.length) return;
    let width = 150;
    ctx.font = '700 12px ' + FONT;
    for (const line of lines) {
      width = Math.max(width, ctx.measureText(line.text).width + 32);
    }
    const h = 18 + lines.length * 16;
    const x = (W - width) / 2;
    const y = TOP + MAZE_H / 2 - h / 2;
    panel(x, y, width, h, accent);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    lines.forEach((line, i) => {
      ctx.font = '700 ' + (line.size || 12) + 'px ' + FONT;
      ctx.fillStyle = line.color || COLORS.text;
      ctx.fillText(line.text, W / 2, y + 17 + i * 16);
    });
  }

  function attractLines(view) {
    const lines = [
      { text: 'PATCHMAN', color: COLORS.pac, size: 18 },
      { text: 'CLEAR THE BACKLOG', color: COLORS.dim },
      { text: 'ARROWS OR WASD TO MOVE' },
      { text: 'GRAB A LOGO TO PATCH THEM' },
    ];
    if (view.best > 0) lines.push({ text: 'BEST ' + view.best, color: COLORS.gold });
    return lines;
  }

  function overlayLines(view) {
    if (view.statusLines.length) return view.statusLines;
    const sim = view.sim;
    if (!sim) return attractLines(view);
    if (sim.state === STATE.IDLE) {
      return [{ text: 'PRESS A DIRECTION', color: COLORS.accent },
        { text: 'TO DEPLOY', color: COLORS.dim }];
    }
    if (sim.state === STATE.READY) return [{ text: 'READY', color: COLORS.gold, size: 16 }];
    if (sim.state === STATE.CLEAR) {
      return [{ text: 'BOARD CLEARED', color: COLORS.accent, size: 14 },
        { text: 'LEVEL ' + sim.level + ' INCOMING', color: COLORS.dim }];
    }
    if (sim.state === STATE.DEAD) {
      const lines = [{ text: 'BREACHED', color: COLORS.warn, size: 16 },
        { text: 'SCORE ' + sim.score }];
      if (view.badge) lines.push({ text: view.badge.label, color: COLORS.gold });
      lines.push({ text: 'PRESS A DIRECTION TO RETRY', color: COLORS.dim });
      return lines;
    }
    return [];
  }

  // ------------------------------------------------------------------------

  function drawLoading(message, bad) {
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.fillStyle = COLORS.void;
    ctx.fillRect(0, 0, W, H);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '700 12px ' + FONT;
    ctx.fillStyle = bad ? COLORS.warn : COLORS.dim;
    ctx.fillText(message, W / 2, H / 2);
  }

  function render(view) {
    const sim = view.sim;
    const nowMs = view.nowMs;

    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.fillStyle = COLORS.void;
    ctx.fillRect(0, 0, W, H);

    // A death shakes the board, which is the only feedback that does not need
    // reading.
    let shakeX = 0;
    let shakeY = 0;
    if (view.shake > 0) {
      shakeX = (Math.random() - 0.5) * 5 * view.shake;
      shakeY = (Math.random() - 0.5) * 5 * view.shake;
    }
    ctx.save();
    ctx.translate(shakeX, shakeY);

    // The board flashes on a level clear, so it is tinted rather than redrawn.
    const clearing = sim && sim.state === STATE.CLEAR;
    const flashOn = clearing && Math.floor(sim.clearTicks / 15) % 2 === 0;
    if (board) {
      if (flashOn) ctx.filter = 'brightness(2.4) saturate(0.4)';
      ctx.drawImage(board, 0, TOP, W, MAZE_H);
      ctx.filter = 'none';
    }

    if (sim) {
      drawPatches(sim, nowMs);
      drawBonus(sim, nowMs);
      const dying = sim.state === STATE.DYING;
      if (!clearing) drawVulns(sim, view.alpha, nowMs);
      if (sim.state !== STATE.DEAD || sim.lives > 0) {
        drawPac(sim, view.alpha, nowMs, dying);
      }
      drawPopups(view.popups, nowMs);
    }
    ctx.restore();

    drawHud(view);
    drawFooter(view);

    const lines = overlayLines(view);
    if (lines.length) {
      let accent = COLORS.accent;
      if (sim && sim.state === STATE.DEAD) accent = COLORS.warn;
      messagePanel(lines, accent);
    }

    if (view.flash > 0) {
      ctx.fillStyle = 'rgba(255, 255, 255, ' + (view.flash * 0.4).toFixed(3) + ')';
      ctx.fillRect(0, 0, W, H);
    }
  }

  return { resize, render, hitsMute, drawLoading };
}
