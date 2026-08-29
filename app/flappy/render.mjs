/**
 * Drawing. Reads the simulation, never writes to it.
 *
 * Every position that moves is interpolated between the previous and current
 * simulation tick, so a 144 Hz display gets smooth motion out of a 120 Hz
 * simulation without either one influencing the other.
 */
import { CONFIG } from './config.mjs';
import { STATE, obstacleAt, visibleRange, duckHitbox } from './sim.mjs';

const COLORS = {
  text: '#eeeeee',
  dim: '#9aa4b2',
  accent: '#4ecca3',
  warn: '#e94560',
  duck: '#ffd23f',
  ink: '#2a2118',
  label: '#8fd0ff',
  panel: 'rgba(10, 18, 34, 0.94)',
};

const MUTE_BOX = { x: 264, y: 6, w: 18, h: 18 };

export function createRenderer(canvas, atlas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let scale = 1;
  let cssW = CONFIG.width;
  let cssH = CONFIG.height;

  /**
   * Size the backing store to a whole number of device pixels per logical
   * pixel. An integer keeps every sprite pixel on a device pixel boundary,
   * which is what stops the art going soft on a high-DPI screen; the CSS box
   * then does the fractional part of the fit.
   */
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const availW = canvas.parentElement.clientWidth;
    const availH = canvas.parentElement.clientHeight;
    const fit = Math.max(0.2, Math.min(availW / CONFIG.width, availH / CONFIG.height));
    scale = Math.max(1, Math.round(fit * dpr));
    cssW = Math.round(CONFIG.width * fit);
    cssH = Math.round(CONFIG.height * fit);
    canvas.width = CONFIG.width * scale;
    canvas.height = CONFIG.height * scale;
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssH + 'px';
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = false;
  }

  /** Turn a pointer event into logical canvas coordinates. */
  function toLogical(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left) * (CONFIG.width / rect.width),
      y: (clientY - rect.top) * (CONFIG.height / rect.height),
    };
  }

  function hitsMute(clientX, clientY) {
    const p = toLogical(clientX, clientY);
    return p.x >= MUTE_BOX.x - 6 && p.x <= MUTE_BOX.x + MUTE_BOX.w + 6
      && p.y >= MUTE_BOX.y - 6 && p.y <= MUTE_BOX.y + MUTE_BOX.h + 6;
  }

  function panel(x, y, w, h, accent) {
    ctx.fillStyle = COLORS.panel;
    ctx.fillRect(x + 2, y, w - 4, h);
    ctx.fillRect(x, y + 2, w, h - 4);
    ctx.fillStyle = accent || COLORS.accent;
    ctx.fillRect(x + 2, y, w - 4, 1);
    ctx.fillRect(x + 2, y + h - 1, w - 4, 1);
    ctx.fillRect(x, y + 2, 1, h - 4);
    ctx.fillRect(x + w - 1, y + 2, 1, h - 4);
  }

  function drawParallax(decorScroll) {
    const bgOff = Math.floor(decorScroll * CONFIG.bgParallax) % CONFIG.width;
    for (let i = -1; i <= 1; i += 1) {
      atlas.draw(ctx, 'bg', i * CONFIG.width - bgOff, CONFIG.groundY - CONFIG.bgH);
    }
  }

  function drawGround(decorScroll) {
    const off = Math.floor(decorScroll * CONFIG.groundParallax) % CONFIG.width;
    for (let i = -1; i <= 1; i += 1) {
      atlas.draw(ctx, 'ground', i * CONFIG.width - off, CONFIG.groundY);
    }
  }

  /** A thin rung across a body tile, so a stacked column reads as server-rack
   * rails rather than a plain green pipe. */
  function drawRackRung(x, y) {
    ctx.strokeStyle = 'rgba(10, 20, 10, 0.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + 3, y + CONFIG.bodyH / 2);
    ctx.lineTo(x + CONFIG.tileW - 3, y + CONFIG.bodyH / 2);
    ctx.stroke();
  }

  function drawColumn(ob) {
    const bodyX = Math.round(ob.x);
    const capX = bodyX - (CONFIG.capW - CONFIG.tileW) / 2;
    const top = Math.round(ob.gapTop);
    const bottom = Math.round(ob.gapBottom);

    // Above the gap: cap first, then body tiles stacked upward off screen.
    atlas.draw(ctx, 'tile_cap_down', capX, top - CONFIG.capH);
    for (let y = top - CONFIG.capH - CONFIG.bodyH; y > -CONFIG.bodyH; y -= CONFIG.bodyH) {
      atlas.draw(ctx, 'tile_body', bodyX, y);
      drawRackRung(bodyX, y);
    }

    // Below the gap: cap, then body tiles down to the ground, clipped so the
    // last tile does not poke through the ground strip.
    atlas.draw(ctx, 'tile_cap_up', capX, bottom);
    for (let y = bottom + CONFIG.capH; y < CONFIG.groundY; y += CONFIG.bodyH) {
      const h = Math.min(CONFIG.bodyH, CONFIG.groundY - y);
      const f = atlas.frame('tile_body');
      ctx.drawImage(atlas.image, f.x, f.y, f.w, h, bodyX, y, f.w, h);
      if (h > CONFIG.bodyH / 2) drawRackRung(bodyX, y);
    }

    // The version label sits above the top cap, on a plate so it reads against
    // the tile art rather than fighting with it. The gap range guarantees at
    // least 34 px of column up there, so it always has somewhere to live.
    const labelY = top - CONFIG.capH - 12;
    const labelW = atlas.textWidth(ob.version) + 6;
    ctx.fillStyle = 'rgba(12, 20, 38, 0.88)';
    ctx.fillRect(Math.round(bodyX + (CONFIG.tileW - labelW) / 2), labelY - 2, labelW, 11);
    atlas.text(ctx, ob.version, bodyX + CONFIG.tileW / 2, labelY + 1,
      { align: 'center', color: '#8fd0ff' });
  }

  function drawDuck(y, angle, frame) {
    const cx = CONFIG.duckX + CONFIG.duckW / 2;
    const cy = y + CONFIG.duckH / 2;
    ctx.save();
    ctx.translate(Math.round(cx), Math.round(cy));
    ctx.rotate(angle);
    atlas.draw(ctx, 'duck_' + frame, -CONFIG.duckW / 2, -CONFIG.duckH / 2);
    ctx.restore();
  }

  function drawScore(score) {
    atlas.text(ctx, 'PATCHES', CONFIG.width / 2, 30,
      { align: 'center', color: COLORS.label, shadow: COLORS.ink });
    atlas.number(ctx, score, CONFIG.width / 2, 42, { align: 'center' });
  }

  function drawMute(muted) {
    atlas.draw(ctx, muted ? 'icon_sound_off' : 'icon_sound_on', MUTE_BOX.x, MUTE_BOX.y);
  }

  function drawAttract(view) {
    atlas.text(ctx, 'FLAPPY DUCK', CONFIG.width / 2, 96,
      { align: 'center', scale: 3, color: COLORS.duck, shadow: COLORS.ink });
    atlas.text(ctx, 'PATCH THE STACK. MISS NOTHING.', CONFIG.width / 2, 132,
      { align: 'center', color: COLORS.dim });

    if (view.best > 0) {
      atlas.text(ctx, 'YOUR BEST', CONFIG.width / 2, 300,
        { align: 'center', color: COLORS.label, shadow: COLORS.ink });
      atlas.number(ctx, view.best, CONFIG.width / 2, 312, { align: 'center' });
    }
    prompt(view, 'TAP TO DEPLOY', 356);
  }

  function drawReady(view) {
    atlas.text(ctx, 'MAINTENANCE WINDOW OPEN', CONFIG.width / 2, 130,
      { align: 'center', color: COLORS.label, shadow: COLORS.ink });
    prompt(view, 'TAP TO DEPLOY', 300);
  }

  function prompt(view, text, y) {
    // A slow blink, driven by wall time so it keeps moving while the world
    // is frozen.
    const on = Math.floor(view.nowMs / 480) % 2 === 0;
    atlas.text(ctx, text, CONFIG.width / 2, y,
      { align: 'center', scale: 2, color: on ? COLORS.accent : COLORS.dim,
        shadow: COLORS.ink });
  }

  function drawGameOver(view) {
    const x = 22;
    const w = CONFIG.width - 44;
    const y = 118;
    const h = 234;
    panel(x, y, w, h, COLORS.warn);

    atlas.text(ctx, 'UPDATE FAILED.', CONFIG.width / 2, y + 16,
      { align: 'center', scale: 2, color: COLORS.warn, shadow: COLORS.ink });
    atlas.text(ctx, 'ROLLING BACK.', CONFIG.width / 2, y + 34,
      { align: 'center', scale: 2, color: COLORS.warn, shadow: COLORS.ink });

    ctx.fillStyle = 'rgba(255,255,255,0.12)';
    ctx.fillRect(x + 14, y + 56, w - 28, 1);

    const badge = view.badge;
    const numbersX = badge ? x + 96 : CONFIG.width / 2;
    const align = badge ? 'left' : 'center';
    if (badge) atlas.draw(ctx, badge.key, x + 26, y + 74);

    atlas.text(ctx, 'PATCHES', numbersX, y + 68,
      { align, color: COLORS.label, shadow: COLORS.ink });
    atlas.number(ctx, view.score, numbersX, y + 78, { align });
    atlas.text(ctx, 'BEST', numbersX, y + 104,
      { align, color: COLORS.label, shadow: COLORS.ink });
    atlas.number(ctx, view.best, numbersX, y + 114, { align });

    if (badge) {
      atlas.text(ctx, badge.label, CONFIG.width / 2, y + 142,
        { align: 'center', color: COLORS.duck, shadow: COLORS.ink });
    }

    ctx.fillStyle = 'rgba(255,255,255,0.12)';
    ctx.fillRect(x + 14, y + 158, w - 28, 1);

    const lines = view.statusLines || [];
    lines.slice(0, 3).forEach((line, i) => {
      atlas.text(ctx, line.text, CONFIG.width / 2, y + 168 + i * 10,
        { align: 'center', color: line.color || COLORS.dim, shadow: COLORS.ink });
    });

    const on = Math.floor(view.nowMs / 480) % 2 === 0;
    atlas.text(ctx, 'TAP TO REDEPLOY', CONFIG.width / 2, y + 210,
      { align: 'center', color: on ? COLORS.accent : COLORS.dim, shadow: COLORS.ink });
  }

  function render(view) {
    const sim = view.sim;
    const scrollX = view.scrollX;

    ctx.fillStyle = '#0f3460';
    ctx.fillRect(0, 0, CONFIG.width, CONFIG.height);

    ctx.save();
    if (view.shake > 0) {
      const decay = view.shake;
      ctx.translate(
        Math.round((Math.random() * 2 - 1) * CONFIG.shakeAmp * decay),
        Math.round((Math.random() * 2 - 1) * CONFIG.shakeAmp * decay));
    }

    drawParallax(view.decorScroll);

    if (sim) {
      const { first, last } = visibleRange(sim, scrollX);
      for (let i = first; i <= last; i += 1) drawColumn(obstacleAt(sim, i, scrollX));
    }

    drawGround(view.decorScroll);
    drawDuck(view.duckY, view.angle, view.wingFrame);
    ctx.restore();

    if (view.showHitbox && sim) {
      const box = duckHitbox(view.duckY);
      ctx.strokeStyle = COLORS.warn;
      ctx.lineWidth = 1;
      ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1);
    }

    if (view.phase === STATE.PLAYING || view.phase === STATE.DYING) drawScore(view.score);
    else if (view.phase === 'attract') drawAttract(view);
    else if (view.phase === STATE.READY) drawReady(view);
    else if (view.phase === STATE.DEAD) drawGameOver(view);

    drawMute(view.muted);

    if (view.flash > 0) {
      ctx.fillStyle = 'rgba(255,255,255,' + (view.flash * 0.8).toFixed(3) + ')';
      ctx.fillRect(0, 0, CONFIG.width, CONFIG.height);
    }
  }

  function drawLoading(message, isError) {
    ctx.fillStyle = '#16213e';
    ctx.fillRect(0, 0, CONFIG.width, CONFIG.height);
    ctx.fillStyle = isError ? COLORS.warn : COLORS.dim;
    ctx.font = '12px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(message, CONFIG.width / 2, CONFIG.height / 2);
  }

  return { resize, render, drawLoading, hitsMute, toLogical, get scale() { return scale; } };
}
