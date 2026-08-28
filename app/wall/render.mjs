/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it. Every sprite is
 * drawn with canvas primitives, matching every other game in this folder.
 *
 * On interpolation. The simulation runs at a fixed 120 ticks a second and the
 * screen does not, so the ball and both paddles carry their previous
 * position and are drawn between the two.
 */
import { CONFIG } from './config.mjs';
import { STATE } from './sim.mjs';

const U = CONFIG.unit;
const px = (su) => su / U;
const WIDTH = CONFIG.width;
const HEIGHT = CONFIG.height + CONFIG.hudTop;

const COLORS = {
  bg: '#101425',
  net: 'rgba(255,255,255,0.16)',
  player: '#36a2eb',
  playerTrim: '#1b6ec2',
  ai: '#e94560',
  aiTrim: '#a5273f',
  ball: '#ffd23f',
  ballRing: '#ff8c1a',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  good: '#8ac926',
  warn: '#e94560',
};

function courtY(y) {
  return CONFIG.hudTop + y;
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

  function drawCourt() {
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.strokeStyle = COLORS.net;
    ctx.lineWidth = 3;
    ctx.setLineDash([12, 10]);
    ctx.beginPath();
    ctx.moveTo(WIDTH / 2, courtY(0));
    ctx.lineTo(WIDTH / 2, courtY(CONFIG.height));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    ctx.font = '600 11px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('NETWORK BOUNDARY', WIDTH / 2, courtY(CONFIG.height / 2) - 10);

    ctx.font = '700 11px "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = 'rgba(54,162,235,0.4)';
    ctx.textAlign = 'left';
    ctx.fillText('BLUE TEAM', 10, courtY(CONFIG.height) - 8);
    ctx.fillStyle = 'rgba(233,69,96,0.4)';
    ctx.textAlign = 'right';
    ctx.fillText('RED TEAM', WIDTH - 10, courtY(CONFIG.height) - 8);
  }

  function drawPaddle(x, y, color, trim) {
    const w = CONFIG.paddleHalfW * 2;
    const h = CONFIG.paddleHalfH * 2;
    ctx.fillStyle = trim;
    ctx.beginPath();
    ctx.roundRect(x - w / 2 - 1, courtY(y) - h / 2 - 1, w + 2, h + 2, 4);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x - w / 2, courtY(y) - h / 2, w, h, 3);
    ctx.fill();
  }

  /**
   * The ball, drawn as the exploit the tagline promises: a packet with a
   * trail behind it, long enough at speed to read as something in transit
   * rather than something merely bouncing.
   */
  function drawBall(x, y, vx = 0, vy = 0) {
    const r = CONFIG.ballHalf;
    const sx = px(vx);
    const sy = px(vy);
    const speed = Math.hypot(sx, sy);
    if (speed > 1) {
      const len = Math.min(22, speed * 0.9);
      const tx = x - (sx / speed) * len;
      const ty = y - (sy / speed) * len;
      const g = ctx.createLinearGradient(tx, courtY(ty), x, courtY(y));
      g.addColorStop(0, 'rgba(255,140,26,0)');
      g.addColorStop(1, 'rgba(255,140,26,0.45)');
      ctx.strokeStyle = g;
      ctx.lineWidth = r * 1.3;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(tx, courtY(ty));
      ctx.lineTo(x, courtY(y));
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.ellipse(x, courtY(y), r + 2, r + 2, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,210,63,0.28)';
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(x, courtY(y), r, r, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.ball;
    ctx.fill();
    ctx.strokeStyle = COLORS.ballRing;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function drawHud(sim, view) {
    ctx.fillStyle = '#0a0a14';
    ctx.fillRect(0, 0, WIDTH, CONFIG.hudTop);
    ctx.textBaseline = 'middle';
    ctx.font = '600 15px "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = COLORS.player;
    ctx.textAlign = 'left';
    ctx.fillText('SCORE ' + sim.score, 10, CONFIG.hudTop / 2);
    ctx.fillStyle = COLORS.hudDim;
    ctx.font = '600 13px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    const lives = Math.max(0, sim.lives);
    ctx.fillText('LIVES ' + '♥'.repeat(Math.min(lives, 6)), WIDTH / 2, CONFIG.hudTop / 2);
    ctx.fillStyle = COLORS.ai;
    ctx.font = '600 15px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('LV ' + sim.level, WIDTH - 10, CONFIG.hudTop / 2);

    drawStatusPanel(view);
  }

  function drawStatusPanel(view) {
    if (!view.statusLines || !view.statusLines.length) return;
    ctx.save();
    ctx.fillStyle = 'rgba(10,10,20,0.75)';
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
    drawCourt();
    drawPaddle(CONFIG.paddleMargin, CONFIG.height / 2 - 60, COLORS.player, COLORS.playerTrim);
    drawPaddle(CONFIG.width - CONFIG.paddleMargin, CONFIG.height / 2 + 40, COLORS.ai, COLORS.aiTrim);
    drawBall(WIDTH / 2, CONFIG.height / 2);
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
      drawCourt();
      const alpha = view.alpha;
      const playerY = px(sim.prevPlayerY + (sim.player.y - sim.prevPlayerY) * alpha);
      const aiY = px(sim.prevAiY + (sim.ai.y - sim.prevAiY) * alpha);
      drawPaddle(CONFIG.paddleMargin, playerY, COLORS.player, COLORS.playerTrim);
      drawPaddle(CONFIG.width - CONFIG.paddleMargin, aiY, COLORS.ai, COLORS.aiTrim);
      if (sim.ball.inPlay || sim.serveTimer < CONFIG.serveDelayTicks) {
        const bx = px(sim.prevBallX + (sim.ball.x - sim.prevBallX) * alpha);
        const by = px(sim.prevBallY + (sim.ball.y - sim.prevBallY) * alpha);
        drawBall(bx, by, sim.ball.vx, sim.ball.vy);
      }
      drawHud(sim, view);
    },
  };
}
