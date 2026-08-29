/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it. Every sprite is
 * drawn with canvas primitives, matching every other game in this folder.
 *
 * On wrapping. The world wraps at its edges, so anything near one edge has
 * to be drawn a second time near the opposite edge or it looks like it
 * vanishes and reappears rather than crosses over. drawWrapped() handles
 * that for every sprite type once, rather than each draw function
 * reimplementing it.
 */
import { CONFIG } from './config.mjs';
import { STATE } from './sim.mjs';

const U = CONFIG.unit;
const px = (su) => su / U;
const WIDTH = CONFIG.width;
const HEIGHT = CONFIG.height;
const CANVAS_H = HEIGHT + CONFIG.hudTop;
const MARGIN = 40;

const COLORS = {
  bg: '#0a0a16',
  star: 'rgba(255,255,255,0.5)',
  ship: '#8ac926',
  shipFlame: '#ff9f40',
  chunk0: '#c08bff',
  chunk1: '#8fd0ff',
  chunk2: '#ff8fa3',
  patch: '#ffd23f',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  warn: '#e94560',
};

const CHUNK_LABEL = { 0: 'LEGACY MONOLITH', 1: 'MODULE', 2: 'DEPENDENCY' };
const CHUNK_COLOR = { 0: COLORS.chunk0, 1: COLORS.chunk1, 2: COLORS.chunk2 };

function shipY(y) {
  return CONFIG.hudTop + y;
}

export function createRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });

  const stars = [];
  for (let i = 0; i < 70; i++) {
    stars.push({
      x: Math.random() * WIDTH,
      y: Math.random() * HEIGHT,
      size: Math.random() < 0.2 ? 2 : 1,
      shade: 0.2 + Math.random() * 0.6,
    });
  }

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const scale = Math.max(0.35, Math.min(box.width / WIDTH, box.height / CANVAS_H));
    canvas.style.width = Math.floor(WIDTH * scale) + 'px';
    canvas.style.height = Math.floor(CANVAS_H * scale) + 'px';
    canvas.width = Math.floor(WIDTH * dpr);
    canvas.height = Math.floor(CANVAS_H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawSpace() {
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, WIDTH, CANVAS_H);
    for (const s of stars) {
      ctx.fillStyle = `rgba(255,255,255,${s.shade})`;
      ctx.fillRect(s.x, shipY(s.y), s.size, s.size);
    }
  }

  /** Calls draw(dx, dy) for the sprite's real position and, when it is
   * within MARGIN of an edge, for the wrapped ghost position too. */
  function drawWrapped(x, y, draw) {
    const xs = [x];
    if (x < MARGIN) xs.push(x + WIDTH);
    if (x > WIDTH - MARGIN) xs.push(x - WIDTH);
    const ys = [y];
    if (y < MARGIN) ys.push(y + HEIGHT);
    if (y > HEIGHT - MARGIN) ys.push(y - HEIGHT);
    for (const dx of xs) for (const dy of ys) draw(dx, dy);
  }

  function drawShip(x, y, heading, thrusting, iframes) {
    drawWrapped(x, y, (dx, dy) => {
      ctx.save();
      ctx.translate(dx, shipY(dy));
      ctx.rotate((heading / 1024) * Math.PI * 2);
      if (iframes > 0 && iframes % 20 < 10) ctx.globalAlpha = 0.35;
      const r = CONFIG.shipHalfW * 1.6;
      ctx.beginPath();
      ctx.moveTo(r, 0);
      ctx.lineTo(-r * 0.7, r * 0.75);
      ctx.lineTo(-r * 0.35, 0);
      ctx.lineTo(-r * 0.7, -r * 0.75);
      ctx.closePath();
      ctx.fillStyle = COLORS.ship;
      ctx.fill();
      ctx.strokeStyle = '#4a6b12';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      // A single eye near the nose -- this is the duck-drone the blurb
      // promises, not a bare wireframe ship.
      ctx.beginPath();
      ctx.arc(r * 0.35, -r * 0.14, 1.6, 0, Math.PI * 2);
      ctx.fillStyle = '#12210a';
      ctx.fill();
      if (thrusting) {
        ctx.beginPath();
        ctx.moveTo(-r * 0.4, r * 0.32);
        ctx.lineTo(-r * 1.3, 0);
        ctx.lineTo(-r * 0.4, -r * 0.32);
        ctx.closePath();
        ctx.fillStyle = COLORS.shipFlame;
        ctx.fill();
      }
      ctx.restore();
    });
  }

  function drawChunk(x, y, size) {
    drawWrapped(x, y, (dx, dy) => {
      const r = CONFIG.chunkHalfW[size];
      ctx.save();
      ctx.translate(dx, shipY(dy));
      ctx.beginPath();
      const spikes = 8;
      for (let i = 0; i < spikes; i++) {
        const a = (i / spikes) * Math.PI * 2;
        const rr = r * (0.8 + 0.2 * ((i * 37) % 5) / 4);
        const px2 = Math.cos(a) * rr;
        const py2 = Math.sin(a) * rr;
        if (i === 0) ctx.moveTo(px2, py2); else ctx.lineTo(px2, py2);
      }
      ctx.closePath();
      ctx.fillStyle = 'rgba(255,255,255,0.05)';
      ctx.fill();
      ctx.strokeStyle = CHUNK_COLOR[size];
      ctx.lineWidth = 2;
      ctx.stroke();
      if (r > 20) {
        ctx.fillStyle = CHUNK_COLOR[size];
        ctx.font = '600 9px "Segoe UI", system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(CHUNK_LABEL[size], 0, 3);
      }
      ctx.restore();
    });
  }

  function drawPatch(x, y) {
    drawWrapped(x, y, (dx, dy) => {
      ctx.beginPath();
      ctx.ellipse(dx, shipY(dy), CONFIG.patchHalfW + 1, CONFIG.patchHalfW + 1, 0, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.patch;
      ctx.fill();
    });
  }

  function drawHud(sim, view) {
    ctx.fillStyle = '#0a0a14';
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
    ctx.fillStyle = 'rgba(10,10,20,0.75)';
    ctx.fillRect(0, CANVAS_H / 2 - 46, WIDTH, 92);
    ctx.textAlign = 'center';
    let y = CANVAS_H / 2 - 46 + 30;
    for (const line of view.statusLines) {
      ctx.font = (line.big ? '700 28px' : '600 15px') + ' "Segoe UI", system-ui, sans-serif';
      ctx.fillStyle = line.color || COLORS.hud;
      ctx.fillText(line.text, WIDTH / 2, y);
      y += line.big ? 34 : 22;
    }
    ctx.restore();
  }

  function drawSplash(view) {
    drawSpace();
    drawShip(WIDTH / 2, HEIGHT / 2, 0, false, 0);
    drawChunk(WIDTH * 0.2, HEIGHT * 0.3, 0);
    drawChunk(WIDTH * 0.8, HEIGHT * 0.65, 1);
    drawChunk(WIDTH * 0.65, HEIGHT * 0.2, 2);
    ctx.fillStyle = '#0a0a14';
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
      drawSpace();
      for (const c of sim.chunks) drawChunk(px(c.x), px(c.y), c.size);
      for (const p of sim.patches) drawPatch(px(p.x), px(p.y));
      if (sim.state !== STATE.DYING) {
        drawShip(px(sim.ship.x), px(sim.ship.y), sim.ship.heading,
          sim.ship.thrusting, sim.ship.iframes);
      }
      drawHud(sim, view);
    },
  };
}
