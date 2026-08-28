/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it. Every sprite is
 * drawn with canvas primitives, matching every other game in this folder.
 * The crosshair that follows the pointer is the one thing drawn here that
 * has no counterpart in the sim at all -- it is purely a hint to the player
 * about where the next shot would land, and the actual shot is whatever
 * coordinate the click itself carried.
 */
import { CONFIG, endpointX } from './config.mjs';
import { STATE } from './sim.mjs';

const U = CONFIG.unit;
const px = (su) => su / U;
const WIDTH = CONFIG.width;
const HEIGHT = CONFIG.height;
const CANVAS_H = HEIGHT + CONFIG.hudTop;

const COLORS = {
  bg: '#0a0f1a',
  ground: '#16324a',
  endpointUp: '#8ac926',
  endpointDown: '#3a3a44',
  missile: '#e94560',
  missileTrail: 'rgba(233,69,96,0.35)',
  silo: '#36a2eb',
  interceptor: '#ffd23f',
  blast: 'rgba(255,210,63,0.35)',
  blastRing: '#ffd23f',
  crosshair: 'rgba(143,208,255,0.7)',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  warn: '#e94560',
};

function worldY(y) {
  return CONFIG.hudTop + y;
}

export function createRenderer(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });

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

  function drawGround(endpoints) {
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, WIDTH, CANVAS_H);
    ctx.fillStyle = COLORS.ground;
    ctx.fillRect(0, worldY(CONFIG.groundY), WIDTH, HEIGHT - CONFIG.groundY);

    for (let i = 0; i < CONFIG.endpointCount; i++) {
      const x = endpointX(i);
      const alive = endpoints[i];
      const w = CONFIG.endpointHalfW;
      ctx.fillStyle = alive ? COLORS.endpointUp : COLORS.endpointDown;
      ctx.beginPath();
      ctx.roundRect(x - w, worldY(CONFIG.groundY) - 16, w * 2, 16, 3);
      ctx.fill();
      if (alive) {
        ctx.fillStyle = '#0a0f1a';
        ctx.font = '600 9px "Segoe UI", system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(CONFIG.endpointLabels[i] || '', x, worldY(CONFIG.groundY) - 7);
      }
    }
  }

  function drawSilo() {
    ctx.fillStyle = COLORS.silo;
    ctx.beginPath();
    ctx.moveTo(CONFIG.siloX - 14, worldY(CONFIG.groundY));
    ctx.lineTo(CONFIG.siloX + 14, worldY(CONFIG.groundY));
    ctx.lineTo(CONFIG.siloX + 8, worldY(CONFIG.siloY) - 4);
    ctx.lineTo(CONFIG.siloX - 8, worldY(CONFIG.siloY) - 4);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = COLORS.hudDim;
    ctx.font = '600 9px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('PATCH HQ', CONFIG.siloX, worldY(CONFIG.groundY) + 11);
  }

  function drawMissile(x, y) {
    ctx.strokeStyle = COLORS.missileTrail;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, worldY(Math.max(0, y - 30)));
    ctx.lineTo(x, worldY(y));
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(x, worldY(y), CONFIG.missileHalfW + 1, CONFIG.missileHalfW + 1, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.missile;
    ctx.fill();
  }

  function drawInterceptor(p) {
    if (!p.exploded) {
      ctx.strokeStyle = 'rgba(255,210,63,0.4)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(CONFIG.siloX, worldY(CONFIG.siloY));
      ctx.lineTo(px(p.x), worldY(px(p.y)));
      ctx.stroke();
      ctx.beginPath();
      ctx.ellipse(px(p.x), worldY(px(p.y)), CONFIG.interceptorHalfW + 1,
        CONFIG.interceptorHalfW + 1, 0, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.interceptor;
      ctx.fill();
      return;
    }
    const frac = p.blastTicksLeft / CONFIG.blastTicks;
    const r = CONFIG.blastRadiusPx * (0.4 + 0.6 * (1 - Math.abs(frac - 0.5) * 2));
    ctx.beginPath();
    ctx.ellipse(px(p.x), worldY(px(p.y)), r, r, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.blast;
    ctx.fill();
    ctx.strokeStyle = COLORS.blastRing;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function drawCrosshair(x, y) {
    if (x == null) return;
    ctx.strokeStyle = COLORS.crosshair;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x - 10, worldY(y));
    ctx.lineTo(x + 10, worldY(y));
    ctx.moveTo(x, worldY(y) - 10);
    ctx.lineTo(x, worldY(y) + 10);
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(x, worldY(y), 5, 5, 0, 0, Math.PI * 2);
    ctx.stroke();
  }

  function drawHud(sim, view) {
    ctx.fillStyle = '#080c14';
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
    const alive = sim.endpoints.filter(Boolean).length;
    ctx.fillText('ENDPOINTS ' + alive + '/' + sim.endpoints.length, WIDTH / 2, CONFIG.hudTop / 2);

    drawStatusPanel(view);
  }

  function drawStatusPanel(view) {
    if (!view.statusLines || !view.statusLines.length) return;
    ctx.save();
    ctx.fillStyle = 'rgba(8,12,20,0.75)';
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
    drawGround(new Array(CONFIG.endpointCount).fill(true));
    drawSilo();
    drawMissile(WIDTH * 0.3, 120);
    drawMissile(WIDTH * 0.7, 220);
    ctx.fillStyle = '#080c14';
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
      drawGround(sim.endpoints);
      drawSilo();
      for (const m of sim.missiles) {
        if (m.delay > 0) continue;
        drawMissile(px(m.x), px(m.y));
      }
      for (const p of sim.interceptors) drawInterceptor(p);
      if (sim.state === STATE.PLAYING) drawCrosshair(view.aimX, view.aimY);
      drawHud(sim, view);
    },
  };
}
