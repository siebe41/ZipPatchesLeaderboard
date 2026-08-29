/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it. Every sprite is
 * drawn with canvas primitives, matching every other game in this folder.
 * A grid game like this one needs no interpolation between ticks -- a step
 * only ever happens once every moveTicks() ticks, and the render loop runs
 * far more often than that, so the head is always exactly where it was last
 * drawn.
 */
import { CONFIG } from './config.mjs';
import { STATE } from './sim.mjs';

const WIDTH = CONFIG.cols * CONFIG.cell;
const HEIGHT = CONFIG.rows * CONFIG.cell + CONFIG.hudTop;

const COLORS = {
  bg: '#0f1a17',
  grid: 'rgba(255,255,255,0.04)',
  body: '#3ec9b0',
  bodyDim: '#2b8f7d',
  duck: '#ffd23f',
  duckBill: '#ff8c1a',
  pmpcBlue: '#1b6ec2',
  pmpcGreen: '#4ecca3',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  warn: '#e94560',
};

function cellX(col) {
  return col * CONFIG.cell;
}

function cellY(row) {
  return CONFIG.hudTop + row * CONFIG.cell;
}

export function createRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const scale = Math.max(0.35, Math.min(box.width / WIDTH, box.height / HEIGHT));
    canvas.style.width = Math.floor(WIDTH * scale) + 'px';
    canvas.style.height = Math.floor(HEIGHT * scale) + 'px';
    canvas.width = Math.floor(WIDTH * dpr);
    canvas.height = Math.floor(HEIGHT * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawGrid() {
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    for (let c = 1; c < CONFIG.cols; c++) {
      ctx.beginPath();
      ctx.moveTo(cellX(c) + 0.5, cellY(0));
      ctx.lineTo(cellX(c) + 0.5, cellY(CONFIG.rows));
      ctx.stroke();
    }
    for (let r = 1; r < CONFIG.rows; r++) {
      ctx.beginPath();
      ctx.moveTo(cellX(0), cellY(r) + 0.5);
      ctx.lineTo(cellX(CONFIG.cols), cellY(r) + 0.5);
      ctx.stroke();
    }
  }

  function drawPatch(col, row) {
    if (col < 0 || row < 0) return;
    const cx = cellX(col) + CONFIG.cell / 2;
    const cy = cellY(row) + CONFIG.cell / 2;
    const r = CONFIG.cell * 0.34;
    ctx.beginPath();
    ctx.ellipse(cx, cy, r, r, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.pmpcBlue;
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.45, cy + r * 0.05);
    ctx.lineTo(cx - r * 0.1, cy + r * 0.4);
    ctx.lineTo(cx + r * 0.5, cy - r * 0.35);
    ctx.strokeStyle = COLORS.pmpcGreen;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();
  }

  function drawBody(col, row) {
    const pad = 2;
    ctx.fillStyle = COLORS.bodyDim;
    ctx.beginPath();
    ctx.roundRect(cellX(col) + pad, cellY(row) + pad,
      CONFIG.cell - pad * 2, CONFIG.cell - pad * 2, 5);
    ctx.fill();
    const pad2 = 4;
    ctx.fillStyle = COLORS.body;
    ctx.beginPath();
    ctx.roundRect(cellX(col) + pad2, cellY(row) + pad2,
      CONFIG.cell - pad2 * 2, CONFIG.cell - pad2 * 2, 4);
    ctx.fill();
    // A small patched-endpoint mark, so the chain reads as a line of deployed
    // fixes rather than an anonymous snake body.
    ctx.beginPath();
    ctx.arc(cellX(col) + CONFIG.cell / 2, cellY(row) + CONFIG.cell / 2, 2, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.pmpcGreen;
    ctx.fill();
  }

  function drawHead(col, row, dir) {
    const cx = cellX(col) + CONFIG.cell / 2;
    const cy = cellY(row) + CONFIG.cell / 2;
    const r = CONFIG.cell * 0.42;
    // dir: 0 up, 1 down, 2 left, 3 right -- the bill points that way.
    const angle = [-Math.PI / 2, Math.PI / 2, Math.PI, 0][dir];
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.ellipse(0, 0, r, r * 0.9, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.duck;
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(r * 0.5, -r * 0.35);
    ctx.lineTo(r * 1.15, 0);
    ctx.lineTo(r * 0.5, r * 0.35);
    ctx.closePath();
    ctx.fillStyle = COLORS.duckBill;
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(r * 0.15, -r * 0.4, 2, 2, 0, 0, Math.PI * 2);
    ctx.fillStyle = '#1a1a2e';
    ctx.fill();
    ctx.restore();
  }

  function drawHud(sim, view) {
    ctx.fillStyle = '#0a140f';
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
    ctx.fillText('LIVES ' + '♥'.repeat(Math.min(lives, 6)), WIDTH / 2, CONFIG.hudTop / 2);

    drawStatusPanel(view);
  }

  function drawStatusPanel(view) {
    if (!view.statusLines || !view.statusLines.length) return;
    ctx.save();
    ctx.fillStyle = 'rgba(10,20,15,0.75)';
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

  function drawSplash(view) {
    drawGrid();
    const midRow = Math.floor(CONFIG.rows / 2);
    const midCol = Math.floor(CONFIG.cols / 2);
    for (let i = 1; i <= 3; i++) drawBody(midCol - i, midRow);
    drawHead(midCol, midRow, 3);
    drawPatch(midCol + 4, midRow);
    ctx.fillStyle = '#0a140f';
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
      drawGrid();
      drawPatch(sim.patch.col, sim.patch.row);
      for (let i = sim.segments.length - 1; i >= 1; i--) {
        drawBody(sim.segments[i].col, sim.segments[i].row);
      }
      if (sim.segments.length && sim.state !== STATE.DYING) {
        drawHead(sim.segments[0].col, sim.segments[0].row, sim.dir);
      } else if (sim.segments.length) {
        ctx.globalAlpha = 0.5;
        drawHead(sim.segments[0].col, sim.segments[0].row, sim.dir);
        ctx.globalAlpha = 1;
      }
      drawHud(sim, view);
    },
  };
}
