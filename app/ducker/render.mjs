/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it, which is what
 * lets the same simulation run headless on the server. Anything needed only
 * to make a frame look right lives in the view object this module is
 * handed, never in the sim, because anything stored in the sim has to be
 * reproduced exactly in Python.
 *
 * Every sprite is drawn with canvas primitives rather than loaded from a
 * sheet, matching every other game in this folder: it keeps the game to a
 * handful of text files and means there is no sprite sheet to keep in step
 * with the code.
 *
 * On interpolation. The simulation runs at a fixed 120 ticks a second and the
 * screen does not, so the duck carries its previous row and column and is
 * drawn between the two. Lane traffic is dense and continuous enough that
 * one tick of jitter does not read as a stutter, so only the duck bothers.
 */
import { CONFIG, ROAD_ROWS, RIVER_ROWS } from './config.mjs';
import { STATE } from './sim.mjs';

const U = CONFIG.unit;
const px = (su) => su / U;
const WIDTH = CONFIG.cols * CONFIG.cell;
const HEIGHT = CONFIG.rows * CONFIG.cell + CONFIG.hudTop;

const COLORS = {
  duck: '#ffd23f',
  duckBill: '#ff8c1a',
  duckDark: '#e0a800',
  road: '#2b2b3a',
  roadLine: '#44445a',
  river: '#164a6b',
  riverShine: '#1f6690',
  grass: '#1e5631',
  grassDark: '#194a29',
  raft: '#eef1f5',
  raftTrim: '#aebccb',
  beetle: '#8ac926',
  beetleTrim: '#5e9412',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  good: '#8ac926',
  warn: '#e94560',
  gold: '#ffd23f',
  // The blue-circle, green-check mark is Patch My PC's own "this is a patch"
  // motif -- the same one Patchaga's front-door icon draws. Reusing it on
  // the raft and the goal slots is what makes a raft read as a patch note
  // and a goal slot read as a PMPC logo, rather than needing a caption.
  pmpcBlue: '#1b6ec2',
  pmpcGreen: '#4ecca3',
};

function rowY(row) {
  return CONFIG.hudTop + row * CONFIG.cell;
}

function colX(col) {
  return col * CONFIG.cell;
}

export function createRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let cssW = WIDTH;
  let cssH = HEIGHT;

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const scale = Math.max(0.35, Math.min(box.width / WIDTH, box.height / HEIGHT));
    cssW = Math.floor(WIDTH * scale);
    cssH = Math.floor(HEIGHT * scale);
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssH + 'px';
    canvas.width = Math.floor(WIDTH * dpr);
    canvas.height = Math.floor(HEIGHT * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /** The lane bands, hedges and dashed road lines. Needs no live run. */
  function drawTerrain() {
    ctx.fillStyle = COLORS.grass;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);

    ctx.fillStyle = COLORS.grassDark;
    ctx.fillRect(0, rowY(CONFIG.goalRow), WIDTH, CONFIG.cell);

    for (const row of RIVER_ROWS) {
      ctx.fillStyle = row % 2 === 0 ? COLORS.river : COLORS.riverShine;
      ctx.fillRect(0, rowY(row), WIDTH, CONFIG.cell);
    }

    ctx.fillStyle = COLORS.grassDark;
    ctx.fillRect(0, rowY(CONFIG.medianRow), WIDTH, CONFIG.cell);

    for (const row of ROAD_ROWS) {
      ctx.fillStyle = COLORS.road;
      ctx.fillRect(0, rowY(row), WIDTH, CONFIG.cell);
      ctx.strokeStyle = COLORS.roadLine;
      ctx.lineWidth = 2;
      ctx.setLineDash([10, 10]);
      ctx.beginPath();
      const midY = rowY(row) + CONFIG.cell / 2;
      ctx.moveTo(0, midY);
      ctx.lineTo(WIDTH, midY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.fillStyle = COLORS.grassDark;
    ctx.fillRect(0, rowY(CONFIG.startRow), WIDTH, CONFIG.cell);
  }

  /**
   * One goal slot: a PMPC roundel, lit up once it has been claimed.
   *
   * Drawn at a fixed size rather than derived from CONFIG.slotHalfW: the hop
   * grid means the *hit* tolerance has to cover a full cell either way for
   * landing to feel fair (see the comment on slotHalfW in config.mjs), but
   * drawing the roundel that wide would spill into the neighbouring column
   * and read as one slot doing the job of two.
   */
  function drawSlot(cx, cy, filled) {
    const r = 18;
    if (filled) {
      ctx.beginPath();
      ctx.ellipse(cx, cy, r + 5, r * 0.62 + 4, 0, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 210, 63, 0.35)';
      ctx.fill();
    }
    ctx.beginPath();
    ctx.ellipse(cx, cy, r, r * 0.62, 0, 0, Math.PI * 2);
    ctx.fillStyle = filled ? COLORS.pmpcBlue : 'rgba(27, 110, 194, 0.5)';
    ctx.fill();
    ctx.strokeStyle = filled ? COLORS.gold : 'rgba(255, 255, 255, 0.25)';
    ctx.lineWidth = filled ? 2 : 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.4, cy + r * 0.05);
    ctx.lineTo(cx - r * 0.06, cy + r * 0.32);
    ctx.lineTo(cx + r * 0.42, cy - r * 0.3);
    ctx.strokeStyle = filled ? COLORS.pmpcGreen : 'rgba(78, 204, 163, 0.55)';
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
  }

  function drawSlots(slotsFilled) {
    CONFIG.slotCols.forEach((c, i) => {
      drawSlot(colX(c) + CONFIG.cell / 2, rowY(CONFIG.goalRow) + CONFIG.cell / 2,
        !!slotsFilled[i]);
    });
  }

  /** A beetle: domed shell with a seam and a highlight, a head out front
   * with antennae, and legs bent at a knee so the silhouette reads as an
   * insect rather than a pill with dots on it. */
  function drawBeetle(x, y, halfW, dir) {
    const w = halfW * 2;
    const h = CONFIG.cell * 0.58;
    const headR = h * 0.3;
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(dir >= 0 ? 1 : -1, 1);

    ctx.strokeStyle = COLORS.beetleTrim;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    for (const lx of [-w * 0.22, -w * 0.02, w * 0.18]) {
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(lx, side * h * 0.26);
        ctx.lineTo(lx - 3, side * h * 0.46);
        ctx.lineTo(lx + 3, side * h * 0.56);
        ctx.stroke();
      }
    }

    ctx.beginPath();
    ctx.ellipse(-w * 0.06, 0, w * 0.46, h * 0.5, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.beetle;
    ctx.fill();
    ctx.strokeStyle = COLORS.beetleTrim;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(-w * 0.1, -h * 0.16, w * 0.28, h * 0.2, 0, Math.PI, 0);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-w * 0.48, 0);
    ctx.lineTo(w * 0.32, 0);
    ctx.strokeStyle = COLORS.beetleTrim;
    ctx.lineWidth = 1;
    ctx.stroke();

    // A hazard light on the shell -- this is a vulnerability, not just a bug.
    ctx.beginPath();
    ctx.arc(-w * 0.08, -h * 0.18, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = '#ff5a5a';
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.35)';
    ctx.lineWidth = 0.8;
    ctx.stroke();

    const hx = w * 0.32;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(hx + headR * 0.4, side * headR * 0.3);
      ctx.quadraticCurveTo(hx + headR * 1.5, side * headR * 1.5,
        hx + headR * 2.1, side * headR * 2.0);
      ctx.strokeStyle = COLORS.beetleTrim;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.ellipse(hx, 0, headR, headR * 0.85, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.beetleTrim;
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(hx + headR * 0.2, -headR * 0.32, 1.7, 1.7, 0, 0, Math.PI * 2);
    ctx.ellipse(hx + headR * 0.2, headR * 0.32, 1.7, 1.7, 0, 0, Math.PI * 2);
    ctx.fillStyle = '#ffd23f';
    ctx.fill();

    ctx.restore();
  }

  /** A raft: a patch-note card carrying the same PMPC badge as a goal slot,
   * so it reads as a patch to ride rather than an unlabelled log. */
  function drawRaft(x, y, halfW) {
    const w = halfW * 2;
    const h = CONFIG.cell * 0.56;

    ctx.beginPath();
    ctx.ellipse(x, y + h * 0.58, w * 0.4, h * 0.2, 0, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.beginPath();
    ctx.roundRect(x - w / 2, y - h / 2, w, h, 7);
    ctx.fillStyle = COLORS.raft;
    ctx.fill();
    ctx.strokeStyle = COLORS.raftTrim;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(x - w * 0.36, y - h * 0.28);
    ctx.lineTo(x - w * 0.1, y - h * 0.28);
    ctx.moveTo(x - w * 0.36, y + h * 0.24);
    ctx.lineTo(x - w * 0.14, y + h * 0.24);
    ctx.strokeStyle = COLORS.raftTrim;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.stroke();

    const r = h * 0.3;
    ctx.beginPath();
    ctx.ellipse(x + w * 0.18, y, r, r, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.pmpcBlue;
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(x + w * 0.18 - r * 0.42, y + r * 0.06);
    ctx.lineTo(x + w * 0.18 - r * 0.08, y + r * 0.38);
    ctx.lineTo(x + w * 0.18 + r * 0.46, y - r * 0.32);
    ctx.strokeStyle = COLORS.pmpcGreen;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
  }

  function drawLanes(sim, alpha) {
    for (const lane of sim.roadLanes) {
      for (const e of lane.entities) {
        const drawX = e.x / U - lane.dir * lane.speedSu / U * (1 - alpha);
        drawBeetle(drawX, rowY(lane.row) + CONFIG.cell / 2, e.halfW, lane.dir);
      }
    }
    for (const lane of sim.riverLanes) {
      for (const e of lane.entities) {
        const drawX = e.x / U - lane.dir * lane.speedSu / U * (1 - alpha);
        drawRaft(drawX, rowY(lane.row) + CONFIG.cell / 2, e.halfW);
      }
    }
  }

  function drawDuck(x, y, dying) {
    const r = CONFIG.cell * 0.34;
    ctx.save();
    ctx.translate(x, y);
    if (dying) ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.ellipse(0, 2, r, r * 0.8, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.duckDark;
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(0, -2, r, r * 0.85, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.duck;
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(-r * 0.35, -r * 0.75, r * 0.55, r * 0.5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(-r * 0.85, -r * 0.75);
    ctx.lineTo(-r * 1.35, -r * 0.6);
    ctx.lineTo(-r * 0.85, -r * 0.45);
    ctx.closePath();
    ctx.fillStyle = COLORS.duckBill;
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(-r * 0.55, -r * 0.9, 2.2, 2.2, 0, 0, Math.PI * 2);
    ctx.fillStyle = '#1a1a2e';
    ctx.fill();
    ctx.restore();
  }

  function drawRunningDuck(sim, alpha) {
    const duck = sim.duck;
    const x = px(sim.prevDuckX) + (px(duck.x) - px(sim.prevDuckX)) * alpha;
    const y = rowY(sim.prevDuckRow) + (rowY(duck.row) - rowY(sim.prevDuckRow)) * alpha
      + CONFIG.cell / 2;
    drawDuck(x, y, sim.state === STATE.DYING);
  }

  function drawHud(sim, view) {
    ctx.fillStyle = '#0f0f1a';
    ctx.fillRect(0, 0, WIDTH, CONFIG.hudTop);
    ctx.fillStyle = COLORS.hud;
    ctx.font = '600 15px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillText('SCORE ' + sim.score, 10, CONFIG.hudTop / 2);
    ctx.textAlign = 'right';
    ctx.fillText('LV ' + sim.level, WIDTH - 10, CONFIG.hudTop / 2);
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.hudDim;
    ctx.font = '600 13px "Segoe UI", system-ui, sans-serif';
    const lives = Math.max(0, sim.lives);
    ctx.fillText('LIVES ' + '♥'.repeat(Math.min(lives, 5)), WIDTH / 2, CONFIG.hudTop / 2);

    if (sim.state === STATE.PLAYING) {
      const frac = Math.max(0, sim.lifeTicksLeft / CONFIG.lifeTicks);
      const barW = WIDTH - 20;
      ctx.fillStyle = 'rgba(255,255,255,0.12)';
      ctx.fillRect(10, CONFIG.hudTop - 4, barW, 2);
      ctx.fillStyle = frac < 0.25 ? COLORS.warn : COLORS.good;
      ctx.fillRect(10, CONFIG.hudTop - 4, barW * frac, 2);
    }

    drawStatusPanel(view);
  }

  function drawStatusPanel(view) {
    if (!view.statusLines || !view.statusLines.length) return;
    ctx.save();
    ctx.fillStyle = 'rgba(15,15,26,0.72)';
    ctx.fillRect(0, HEIGHT / 2 - 46, WIDTH, 92);
    ctx.textAlign = 'center';
    let y = HEIGHT / 2 - 46 + 30;
    for (const line of view.statusLines) {
      ctx.font = (line.big ? '700 28px' : '600 15px') + ' "Segoe UI", system-ui, sans-serif';
      ctx.fillStyle = line.color || COLORS.hud;
      ctx.fillText(line.text, WIDTH / 2, y);
      y += line.big ? 34 : 22;
    }
    ctx.restore();
  }

  /**
   * Shown before a run exists, so the canvas is never just a black
   * rectangle waiting for a keypress: a still frame of the crossing, empty
   * slots, and the title panel over it.
   */
  function drawSplash(view) {
    drawTerrain();
    drawSlots([false, false, false, false, false]);
    drawBeetle(colX(9) + CONFIG.cell / 2, rowY(ROAD_ROWS[1]) + CONFIG.cell / 2,
      CONFIG.roadEntityHalfW, 1);
    drawBeetle(colX(3) + CONFIG.cell / 2, rowY(ROAD_ROWS[3]) + CONFIG.cell / 2,
      CONFIG.roadEntityHalfW, -1);
    drawRaft(colX(4) + CONFIG.cell / 2, rowY(RIVER_ROWS[1]) + CONFIG.cell / 2,
      CONFIG.riverEntityHalfW);
    drawRaft(colX(10) + CONFIG.cell / 2, rowY(RIVER_ROWS[3]) + CONFIG.cell / 2,
      CONFIG.riverEntityHalfW);
    drawDuck(colX(CONFIG.startCol) + CONFIG.cell / 2,
      rowY(CONFIG.startRow) + CONFIG.cell / 2, false);
    ctx.fillStyle = '#0f0f1a';
    ctx.fillRect(0, 0, WIDTH, CONFIG.hudTop);
    drawStatusPanel(view);
  }

  return {
    resize,
    render(view) {
      const sim = view.sim;
      if (!sim) {
        drawSplash(view);
        return;
      }
      drawTerrain();
      drawSlots(sim.slotsFilled);
      drawLanes(sim, view.alpha);
      drawRunningDuck(sim, view.alpha);
      drawHud(sim, view);
    },
  };
}
