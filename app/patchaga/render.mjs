/**
 * Drawing. Nothing in here affects the run.
 *
 * The renderer reads the simulation and never writes to it, which is what lets
 * the same simulation run headless on the server. If a value is needed only to
 * make something look right -- a shake, a flash, a floating score -- it lives
 * in the view object this module is handed, never in the sim, because anything
 * stored in the sim has to be reproduced exactly in Python.
 *
 * Every sprite is drawn with canvas primitives rather than loaded from a sheet.
 * That keeps the game to a handful of text files, lets colours be derived from
 * state rather than baked in, and means there is no sprite sheet to keep in
 * step with the code.
 *
 * On interpolation. The simulation runs at a fixed 120 ticks a second and the
 * screen does not, so entities carry their previous position and are drawn
 * between the two. Without it a 60Hz display shows every other tick and the
 * motion visibly stutters; with it the same run looks smooth on any refresh
 * rate, and the run itself is unchanged either way.
 */
import { CONFIG, isSweepWave } from './config.mjs';
import { STATE, BUG, KIND, formX, formY } from './sim.mjs';

const U = CONFIG.unit;

const COLORS = {
  duck: '#ffd23f',
  duckBill: '#ff8c1a',
  duckDark: '#e0a800',
  patch: '#7ac143',
  patchRing: '#1a86c8',
  patchCore: '#ffffff',
  beam: '#ff6b4a',
  bugShot: '#ff8fa3',
  hud: '#eeeeee',
  hudDim: '#9aa4b2',
  wave: '#8fd0ff',
  good: '#4ecca3',
  warn: '#e94560',
};

// One palette per bug type. Ordered to match KIND, so a kind indexes directly.
const BUGS = [
  { body: '#8fd0ff', trim: '#3a86c8', eye: '#0b1a2a', name: 'DRONE' },
  { body: '#c08bff', trim: '#7a3fd0', eye: '#1a0b2a', name: 'WEEVIL' },
  { body: '#ff6b4a', trim: '#c03a1e', eye: '#2a0b06', name: 'ROOTKIT' },
];

/** Loads an image, resolving to null rather than rejecting if it is missing. */
export function loadImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

export function createRenderer(canvas, logo) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let cssW = CONFIG.width;
  let cssH = CONFIG.height;

  // The starfield is cosmetic, so it uses Math.random freely. Using the sim's
  // generator here would consume from the stream the server replays and change
  // the game, which is the kind of bug that only shows up as a rejected score.
  const stars = [];
  for (let i = 0; i < 90; i++) {
    stars.push({
      x: Math.random() * CONFIG.width,
      y: Math.random() * (CONFIG.height - CONFIG.hudTop) + CONFIG.hudTop,
      speed: 0.10 + Math.random() * 0.55,
      size: Math.random() < 0.15 ? 2 : 1,
      shade: 0.25 + Math.random() * 0.6,
    });
  }

  /**
   * Fit the canvas to its box at device resolution.
   *
   * The backing store is sized in device pixels and the transform scales the
   * game's own 432x560 coordinates onto it, so every drawing call below can be
   * written in game units and still come out sharp on a high-DPI screen.
   */
  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const scale = Math.max(0.35,
      Math.min(box.width / CONFIG.width, box.height / CONFIG.height));
    cssW = Math.floor(CONFIG.width * scale);
    cssH = Math.floor(CONFIG.height * scale);
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssH + 'px';
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
  }

  function begin() {
    const sx = canvas.width / CONFIG.width;
    const sy = canvas.height / CONFIG.height;
    ctx.setTransform(sx, 0, 0, sy, 0, 0);
  }

  // --- Sprites ------------------------------------------------------------ #

  /**
   * The duck. A body, a head, a bill and an eye, facing up the screen.
   *
   * ``lean`` tilts it into the direction of travel. It is drawn slightly larger
   * than its hitbox on purpose: a shot that looks like it grazed the duck and
   * did not kill it reads as generous, whereas the reverse reads as broken.
   */
  function drawDuck(x, y, lean, scale = 1, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    ctx.rotate(lean * 0.18);
    ctx.scale(scale, scale);

    // Body
    ctx.fillStyle = COLORS.duck;
    ctx.beginPath();
    ctx.ellipse(0, 3, 9, 6.5, 0, 0, Math.PI * 2);
    ctx.fill();

    // Tail
    ctx.beginPath();
    ctx.moveTo(-8, 2);
    ctx.lineTo(-13, -1);
    ctx.lineTo(-8, 6);
    ctx.closePath();
    ctx.fill();

    // Head
    ctx.beginPath();
    ctx.arc(2.5, -5, 5.2, 0, Math.PI * 2);
    ctx.fill();

    // Bill, pointing up the screen because that is where it is shooting
    ctx.fillStyle = COLORS.duckBill;
    ctx.beginPath();
    ctx.moveTo(0.5, -9);
    ctx.lineTo(4.5, -9);
    ctx.lineTo(2.5, -14);
    ctx.closePath();
    ctx.fill();

    // Underside shading, so it does not read as a flat blob
    ctx.fillStyle = COLORS.duckDark;
    ctx.beginPath();
    ctx.ellipse(0, 7, 7.5, 2.4, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#1a1a2e';
    ctx.beginPath();
    ctx.arc(4, -6.2, 1.15, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }

  /**
   * A patch: the Patch My PC mark, small enough to read as a bullet.
   *
   * The real mark is a blue aperture ring around a monitor with a green check
   * on it, and at eight pixels across all three of those become one smudge. So
   * this draws only the check. It is the part of the logo that survives being
   * shrunk -- two strokes, high contrast, a shape nothing else on screen has --
   * and it is also the part that means something here, since the whole point is
   * that the duck is firing patches at bugs.
   *
   * The blue disc behind it is the ring, reduced to the only thing a ring can
   * still say at this size: that the check sits on top of something round and
   * Patch My PC blue.
   */
  function drawPatch(x, y) {
    ctx.save();
    ctx.translate(x, y);

    // The trail, which is what actually makes a bullet readable in motion. It
    // is drawn first and behind, so the mark itself stays crisp.
    const trail = ctx.createLinearGradient(0, -2, 0, 12);
    trail.addColorStop(0, 'rgba(122,193,67,0.55)');
    trail.addColorStop(1, 'rgba(122,193,67,0)');
    ctx.fillStyle = trail;
    ctx.fillRect(-1.6, -2, 3.2, 14);

    ctx.fillStyle = COLORS.patchRing;
    ctx.beginPath();
    ctx.arc(0, 0, 5, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = COLORS.patch;
    ctx.lineWidth = 2.4;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(-2.6, 0.1);
    ctx.lineTo(-0.7, 2.2);
    ctx.lineTo(2.9, -2.6);
    ctx.stroke();

    ctx.restore();
  }

  /**
   * A bug. Six legs, two wings, a body and a pair of eyes.
   *
   * ``wing`` is a phase, so the wings beat continuously rather than flicking
   * between two frames, and the whole formation does not beat in unison.
   */
  function drawBug(x, y, kind, wing, flipped, alpha = 1) {
    const c = BUGS[kind] || BUGS[0];
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    if (flipped) ctx.rotate(Math.PI);

    const beat = Math.sin(wing) * 0.35;

    // Wings, behind the body
    ctx.fillStyle = c.trim;
    ctx.globalAlpha = alpha * 0.75;
    for (const side of [-1, 1]) {
      ctx.save();
      ctx.translate(side * 5, -1);
      ctx.rotate(side * (0.5 + beat));
      ctx.beginPath();
      ctx.ellipse(side * 5, 0, 6.5, 3, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.globalAlpha = alpha;

    // Legs
    ctx.strokeStyle = c.trim;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    for (const side of [-1, 1]) {
      for (let i = 0; i < 3; i++) {
        const ly = -3 + i * 3.2;
        ctx.moveTo(side * 3, ly);
        ctx.lineTo(side * 8.5, ly + 2.2 + Math.sin(wing + i) * 0.8);
      }
    }
    ctx.stroke();

    // Body
    ctx.fillStyle = c.body;
    ctx.beginPath();
    ctx.ellipse(0, 1, 6, 7.5, 0, 0, Math.PI * 2);
    ctx.fill();

    // Shell seam
    ctx.strokeStyle = c.trim;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, -4);
    ctx.lineTo(0, 7);
    ctx.stroke();

    // Head
    ctx.fillStyle = c.trim;
    ctx.beginPath();
    ctx.arc(0, -6, 3.6, 0, Math.PI * 2);
    ctx.fill();

    // Eyes
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(-1.7, -6.4, 1.3, 0, Math.PI * 2);
    ctx.arc(1.7, -6.4, 1.3, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = c.eye;
    ctx.beginPath();
    ctx.arc(-1.7, -6.4, 0.6, 0, Math.PI * 2);
    ctx.arc(1.7, -6.4, 0.6, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // --- Layers ------------------------------------------------------------- #

  function drawBackground(view, dt) {
    const g = ctx.createLinearGradient(0, 0, 0, CONFIG.height);
    g.addColorStop(0, '#0b1026');
    g.addColorStop(0.6, '#111a3a');
    g.addColorStop(1, '#0a0f24');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, CONFIG.width, CONFIG.height);

    for (const s of stars) {
      s.y += s.speed * dt * 0.06;
      if (s.y > CONFIG.height) {
        s.y = CONFIG.hudTop;
        s.x = Math.random() * CONFIG.width;
      }
      ctx.globalAlpha = s.shade;
      ctx.fillStyle = '#cfe4ff';
      ctx.fillRect(s.x, s.y, s.size, s.size);
    }
    ctx.globalAlpha = 1;

    // The logo, as a watermark. It is the only real artwork in the game and it
    // is decoration only: if it failed to load the playfield is a starfield,
    // which is what it was anyway.
    if (logo) {
      const w = 180;
      const h = w * (logo.height / logo.width);
      ctx.globalAlpha = 0.045;
      ctx.drawImage(logo, (CONFIG.width - w) / 2, 250 - h / 2, w, h);
      ctx.globalAlpha = 1;
    }
  }

  function drawBeam(sim, bug, nowMs) {
    // Nothing is catchable during the windup, so the beam grows into place
    // first. Drawing it at full width immediately would make an unavoidable
    // capture look like an avoidable one.
    const grow = Math.min(1, bug.t / CONFIG.beamWindup);
    const top = bug.y / U + 6;
    const bottom = CONFIG.duckY + 8;
    const half = CONFIG.beamHalfW * grow;
    const x = bug.x / U;
    const flicker = 0.55 + Math.sin(nowMs / 55) * 0.16;

    const g = ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, 'rgba(255, 107, 74, ' + (0.55 * flicker).toFixed(3) + ')');
    g.addColorStop(1, 'rgba(255, 107, 74, 0.02)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.moveTo(x - half * 0.35, top);
    ctx.lineTo(x + half * 0.35, top);
    ctx.lineTo(x + half, bottom);
    ctx.lineTo(x - half, bottom);
    ctx.closePath();
    ctx.fill();

    // Rungs falling down the beam, which is what makes it read as active
    ctx.strokeStyle = 'rgba(255, 190, 160, 0.35)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i++) {
      const f = ((nowMs / 420 + i / 5) % 1);
      const y = top + (bottom - top) * f;
      const w = half * (0.35 + 0.65 * f);
      ctx.globalAlpha = (1 - f) * grow;
      ctx.beginPath();
      ctx.moveTo(x - w, y);
      ctx.lineTo(x + w, y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawHud(sim, view) {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
    ctx.fillRect(0, 0, CONFIG.width, CONFIG.hudTop);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.10)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, CONFIG.hudTop + 0.5);
    ctx.lineTo(CONFIG.width, CONFIG.hudTop + 0.5);
    ctx.stroke();

    ctx.font = 'bold 13px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';

    ctx.fillStyle = COLORS.hudDim;
    ctx.textAlign = 'left';
    ctx.fillText('SCORE', 10, 12);
    ctx.fillStyle = COLORS.hud;
    ctx.font = 'bold 15px "Segoe UI", system-ui, sans-serif';
    ctx.fillText(String(sim.score), 10, 25);

    ctx.font = 'bold 13px "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = COLORS.hudDim;
    ctx.textAlign = 'center';
    ctx.fillText('BEST', CONFIG.width / 2, 12);
    ctx.fillStyle = COLORS.hud;
    ctx.fillText(String(Math.max(view.best, sim.score)), CONFIG.width / 2, 25);

    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.hudDim;
    ctx.fillText('WAVE', CONFIG.width - 10, 12);
    ctx.fillStyle = isSweepWave(sim.wave) ? COLORS.good : COLORS.wave;
    ctx.fillText(String(sim.wave), CONFIG.width - 10, 25);

    // Lives, drawn as the ducks they are. The one in play is not shown, so an
    // empty row means the run ends with this life.
    for (let i = 0; i < sim.lives - 1; i++) {
      drawDuck(118 + i * 22, 18, 0, 0.62, 0.9);
    }

    drawMute(view.muted);
  }

  // The mute control is drawn on the canvas rather than placed beside it so
  // that it works on a phone, where the keyboard shortcut does not exist and
  // the page chrome is scrolled out of the way.
  const MUTE = { x: CONFIG.width - 84, y: 6, w: 22, h: 22 };

  function drawMute(muted) {
    ctx.save();
    ctx.translate(MUTE.x + MUTE.w / 2, MUTE.y + MUTE.h / 2);
    ctx.fillStyle = muted ? COLORS.hudDim : COLORS.hud;
    ctx.beginPath();
    ctx.moveTo(-5, -2.5);
    ctx.lineTo(-2, -2.5);
    ctx.lineTo(1.5, -6);
    ctx.lineTo(1.5, 6);
    ctx.lineTo(-2, 2.5);
    ctx.lineTo(-5, 2.5);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = muted ? COLORS.warn : COLORS.hud;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    if (muted) {
      ctx.moveTo(4, -4); ctx.lineTo(8, 4);
      ctx.moveTo(8, -4); ctx.lineTo(4, 4);
    } else {
      ctx.arc(2.5, 0, 4, -0.9, 0.9);
      ctx.arc(2.5, 0, 7, -0.9, 0.9);
    }
    ctx.stroke();
    ctx.restore();
  }

  /** Whether a page-space point landed on the mute control. */
  function hitsMute(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width * CONFIG.width;
    const y = (clientY - rect.top) / rect.height * CONFIG.height;
    // Padded, because a 22px target is small for a thumb.
    return x >= MUTE.x - 8 && x <= MUTE.x + MUTE.w + 8
      && y >= MUTE.y - 6 && y <= MUTE.y + MUTE.h + 6;
  }

  /**
   * The strip drawn before the first run exists.
   *
   * Without it the page loads to a bare starfield and reads as still loading.
   * It shows the stored best, which is the one number worth seeing before you
   * have played anything this session.
   */
  function drawIdleHud(view) {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
    ctx.fillRect(0, 0, CONFIG.width, CONFIG.hudTop);
    ctx.font = 'bold 13px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.hudDim;
    ctx.fillText('BEST', CONFIG.width / 2, 12);
    ctx.fillStyle = COLORS.hud;
    ctx.fillText(String(view.best), CONFIG.width / 2, 25);
    drawMute(view.muted);
  }

  function drawPanel(lines, nowMs) {
    if (!lines.length) return;
    const h = lines.length * 22 + 22;
    const y = CONFIG.height / 2 - h / 2;
    ctx.fillStyle = 'rgba(6, 10, 24, 0.82)';
    roundRect(36, y, CONFIG.width - 72, h, 10);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.12)';
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    lines.forEach((line, i) => {
      ctx.font = 'bold ' + (line.big ? 19 : 13)
        + 'px "Segoe UI", system-ui, sans-serif';
      ctx.fillStyle = line.color || COLORS.hud;
      ctx.globalAlpha = line.blink ? 0.45 + 0.55 * Math.abs(Math.sin(nowMs / 380)) : 1;
      ctx.fillText(line.text, CONFIG.width / 2, y + 22 + i * 22);
    });
    ctx.globalAlpha = 1;
  }

  // --- The frame ---------------------------------------------------------- #

  /**
   * Draw one frame.
   *
   * ``view.alpha`` is how far the clock has advanced past the last completed
   * tick, so every moving thing is drawn between where it was and where it is.
   */
  function render(view) {
    const sim = view.sim;
    const alpha = view.alpha;
    const dt = view.dt;
    const nowMs = view.nowMs;
    begin();
    drawBackground(view, dt);

    if (!sim) {
      drawIdleHud(view);
      drawPanel(view.statusLines, nowMs);
      return;
    }

    ctx.save();
    if (view.shake > 0) {
      const s = view.shake;
      ctx.translate((Math.random() - 0.5) * s, (Math.random() - 0.5) * s);
    }

    const lerp = (prev, now) => (prev + (now - prev) * alpha) / U;

    // Bugs, behind everything the player controls
    for (const bug of sim.bugs) {
      if (bug.state === BUG.DEAD || bug.state === BUG.WAITING) continue;
      if (bug.state === BUG.SWEEPING && bug.t < 0) continue;
      const bx = lerp(bug.px, bug.x);
      const by = lerp(bug.py, bug.y);
      if (bug.beamOpen) drawBeam(sim, bug, nowMs);
      const inSlot = bug.state === BUG.SLOT;
      drawBug(bx, by, bug.kind, sim.tick / 9 + bug.order, !inSlot && by < 0);
      // A captured duck rides under the rootkit that took it, so the player can
      // see what they are shooting at and why it is worth the risk.
      if (bug.holdsDuck) drawDuck(bx, by + 16, 0, 0.85, 0.95);
    }

    // A freed duck falling home
    if (sim.rescue) {
      drawDuck(sim.rescue.x / U, sim.rescue.y / U,
        Math.sin(nowMs / 90) * 0.5, 0.9, 0.95);
    }

    // Bug fire
    ctx.fillStyle = COLORS.bugShot;
    for (const s of sim.bugShots) {
      const x = lerp(s.px, s.x);
      const y = lerp(s.py, s.y);
      ctx.beginPath();
      ctx.ellipse(x, y, 2, 5, 0, 0, Math.PI * 2);
      ctx.fill();
    }

    // Patches
    for (const p of sim.patches) drawPatch(p.x / U, lerp(p.py, p.y));

    // The duck
    const duck = sim.duck;
    if (duck.alive) {
      const dx = lerp(sim.prevDuckX, duck.x);
      // Blinking while invulnerable is the convention, and it also answers the
      // question a player would otherwise ask out loud: why did that not kill me?
      const vis = duck.invuln > 0 ? (Math.floor(nowMs / 90) % 2 === 0) : true;
      if (vis) {
        const lean = duck.dir;
        if (duck.merged) {
          drawDuck(dx - CONFIG.mergedOffset, CONFIG.duckY, lean);
          drawDuck(dx + CONFIG.mergedOffset, CONFIG.duckY, lean);
        } else {
          drawDuck(dx, CONFIG.duckY, lean);
        }
      }
    }

    // Explosions and floating scores
    for (const p of view.popups) {
      ctx.globalAlpha = Math.max(0, p.life / p.max);
      if (p.kind === 'burst') {
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 2;
        const r = (1 - p.life / p.max) * 18 + 3;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.font = 'bold 12px "Segoe UI", system-ui, sans-serif';
        ctx.fillStyle = p.color;
        ctx.textAlign = 'center';
        ctx.fillText(p.text, p.x, p.y - (1 - p.life / p.max) * 22);
      }
    }
    ctx.globalAlpha = 1;

    ctx.restore();

    if (view.flash > 0) {
      ctx.fillStyle = 'rgba(255, 255, 255, ' + (view.flash * 0.5).toFixed(3) + ')';
      ctx.fillRect(0, CONFIG.hudTop, CONFIG.width, CONFIG.height - CONFIG.hudTop);
    }

    drawHud(sim, view);

    // Panels. The sim's own states get theirs here so they cannot be forgotten;
    // anything the page wants to say arrives in view.statusLines.
    let lines = view.statusLines;
    if (!lines.length) {
      if (sim.state === STATE.READY) {
        lines = [
          { text: isSweepWave(sim.wave) ? 'REGRESSION SWEEP' : 'WAVE ' + sim.wave,
            color: isSweepWave(sim.wave) ? COLORS.good : COLORS.wave, big: true },
          { text: isSweepWave(sim.wave) ? 'CLEAR THEM ALL FOR A BONUS'
            : 'MOVE OR FIRE TO BEGIN', blink: true },
        ];
      } else if (sim.state === STATE.CLEAR) {
        lines = [{ text: 'WAVE CLEARED', color: COLORS.good, big: true }];
      } else if (sim.state === STATE.DEAD) {
        lines = [{ text: 'GAME OVER', color: COLORS.warn, big: true }];
      }
    }
    drawPanel(lines, nowMs);
  }

  resize();
  return {
    render,
    resize,
    hitsMute,
    get width() { return cssW; },
    get height() { return cssH; },
  };
}
