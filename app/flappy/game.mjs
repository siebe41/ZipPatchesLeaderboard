/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
import { randomSeed } from './rng.mjs';
import {
  createSim, queueFlap, step, durationMs, STATE,
} from './sim.mjs';
import { loadAtlas } from './atlas.mjs';
import { createAudio } from './audio.mjs';
import { createRenderer } from './render.mjs';

const BASE = '/flappy/static/';
const API = '/flappy/api/';
const WING_SEQUENCE = [0, 1, 2, 1];
const RESTART_LOCKOUT_MS = 650; // stops the death tap restarting the run

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  playerList: document.getElementById('player-list'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
  seed: document.getElementById('seed'),
};

const view = {
  phase: 'attract',
  sim: null,
  score: 0,
  best: 0,
  duckY: CONFIG.startY,
  scrollX: 0,
  decorScroll: 0,
  angle: 0,
  wingFrame: 1,
  shake: 0,
  flash: 0,
  muted: true,
  nowMs: 0,
  badge: null,
  statusLines: [],
  showHitbox: new URLSearchParams(location.search).has('hitbox'),
};

const run = {
  startMs: 0,
  decorBase: 0,
  deadAtMs: 0,
  wingMs: 0,
  submitted: false,
  posted: false, // a run that reached the board must never reach it twice
};

let atlas = null;
let renderer = null;
let audio = null;
let lastFrameMs = 0;
let announcedBadge = 0;
let rosterNames = [];

function nameKey(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function isKnownPlayer(name) {
  if (!rosterNames.length) return true;
  const key = nameKey(name);
  return !!key && rosterNames.some((p) => nameKey(p) === key);
}

async function loadRoster() {
  try {
    const res = await fetch(API + 'roster');
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !Array.isArray(data.players)) return;
    rosterNames = data.players.filter((p) => typeof p === 'string' && p.trim());
    if (!el.playerList) return;
    el.playerList.textContent = '';
    for (const name of rosterNames) {
      const opt = document.createElement('option');
      opt.value = name;
      el.playerList.appendChild(opt);
    }
  } catch (err) {
    rosterNames = [];
  }
}

// --------------------------------------------------------------------------
// Persisted odds and ends
// --------------------------------------------------------------------------

function readStore(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (err) {
    return fallback; // private mode, or storage disabled. Not worth failing over.
  }
}

function writeStore(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (err) { /* ignore */ }
}

// --------------------------------------------------------------------------
// Status line, shown both on the canvas panel and under it
// --------------------------------------------------------------------------

function setStatus(text, kind) {
  el.status.textContent = text;
  el.status.className = 'status' + (kind ? ' ' + kind : '');
}

function panelLines(lines) {
  view.statusLines = lines;
}

// --------------------------------------------------------------------------
// Run lifecycle
// --------------------------------------------------------------------------

function startRun() {
  const seed = randomSeed();
  view.sim = createSim(seed);
  run.startMs = performance.now();
  run.decorBase = view.decorScroll;
  run.submitted = false;
  run.posted = false;
  run.wingMs = 0;
  announcedBadge = 0;
  view.badge = null;
  view.score = 0;
  view.angle = 0;
  if (el.seed) el.seed.textContent = 'Seed ' + seed;
  panelLines([]);
}

function badgeFor(score) {
  return CONFIG.badges.find((b) => score >= b.at) || null;
}

function onDead() {
  const sim = view.sim;
  run.deadAtMs = performance.now();
  view.badge = badgeFor(sim.score);
  if (sim.score > view.best) {
    view.best = sim.score;
    writeStore(CONFIG.bestKey, view.best);
  }
  submitRun();
}

// --------------------------------------------------------------------------
// Submission
// --------------------------------------------------------------------------

async function submitRun() {
  const sim = view.sim;
  if (!sim || run.submitted || run.posted) return;
  const player = (el.player.value || '').trim();
  if (!player) {
    panelLines([{ text: 'ENTER YOUR NAME BELOW', color: '#8fd0ff' },
      { text: 'TO POST THIS RUN' }]);
    setStatus('Enter your name below to post this run to the board.', '');
    return;
  }
  if (!isKnownPlayer(player)) {
    panelLines([{ text: 'NOT POSTED', color: '#e94560' }]);
    setStatus('Use your exact leaderboard name to post this run.', 'bad');
    return;
  }
  if (sim.score <= 0) {
    panelLines([{ text: 'NO PATCHES DEPLOYED' }, { text: 'NOTHING TO POST' }]);
    setStatus('A run of zero is not posted to the board.', '');
    return;
  }

  run.submitted = true;
  panelLines([{ text: 'POSTING RUN...' }]);
  setStatus('Posting run...', '');
  try {
    const res = await fetch(API + 'score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player,
        score: sim.score,
        seed: sim.seed,
        duration_ms: durationMs(sim),
        flaps: sim.flaps.slice(0, CONFIG.maxFlapTrace),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      run.submitted = false;
      const msg = data.error || ('Submission failed (' + res.status + ')');
      panelLines([{ text: 'NOT POSTED', color: '#e94560' }]);
      setStatus(msg, 'bad');
      return;
    }
    writeStore(CONFIG.playerKey, data.player || player);
    run.posted = true;
    el.player.value = data.player || player;
    const lines = [{ text: 'POSTED AS ' + (data.player || player), color: '#4ecca3' }];
    if (data.rank) lines.push({ text: 'ALL TIME RANK ' + data.rank });
    if (data.personal_best) lines.push({ text: 'NEW PERSONAL BEST', color: '#ffd23f' });
    panelLines(lines);
    setStatus('Posted as ' + (data.player || player)
      + (data.rank ? '. All-time rank ' + data.rank + '.' : '.')
      + (data.personal_best ? ' New personal best.' : ''), 'good');
  } catch (err) {
    run.submitted = false;
    panelLines([{ text: 'NOT POSTED', color: '#e94560' }]);
    setStatus('Could not reach the board. The run is not lost, try again.', 'bad');
  }
}

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

function tickForNow(nowMs) {
  return Math.floor((nowMs - run.startMs) / CONFIG.stepMs);
}

function act(nowMs) {
  const sim = view.sim;
  if (!sim) { startRun(); return; }
  if (sim.state === STATE.READY || sim.state === STATE.PLAYING) {
    queueFlap(sim, tickForNow(nowMs));
    return;
  }
  if (sim.state === STATE.DEAD && nowMs - run.deadAtMs > RESTART_LOCKOUT_MS) {
    startRun();
  }
}

function toggleMute() {
  view.muted = audio.toggle();
  writeStore(CONFIG.mutedKey, view.muted ? '1' : '0');
}

function bindInput() {
  el.canvas.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    audio.unlock();
    if (renderer.hitsMute(ev.clientX, ev.clientY)) { toggleMute(); return; }
    act(performance.now());
  });
  // Suppress the synthetic mouse event and double-tap zoom that follow a touch.
  el.canvas.addEventListener('touchstart', (ev) => ev.preventDefault(), { passive: false });

  window.addEventListener('keydown', (ev) => {
    const tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
    if (ev.code === 'Space' || ev.code === 'ArrowUp') {
      ev.preventDefault();
      if (ev.repeat) return; // holding the key is one flap, not a hundred
      audio.unlock();
      act(performance.now());
    } else if (ev.code === 'KeyM') {
      audio.unlock();
      toggleMute();
    }
  });

  el.player.addEventListener('change', () => {
    writeStore(CONFIG.playerKey, el.player.value.trim());
  });
  el.post.addEventListener('click', () => {
    if (run.posted) {
      setStatus('That run is already on the board.', 'good');
      return;
    }
    run.submitted = false;
    submitRun();
  });
  window.addEventListener('resize', () => renderer.resize());
}

// --------------------------------------------------------------------------
// Presentation
// --------------------------------------------------------------------------

function drainEvents(sim) {
  for (const ev of sim.events) {
    if (ev.type === 'flap') {
      audio.play('flap');
      run.wingMs = 0;
    } else if (ev.type === 'score') {
      audio.play('score');
      const badge = badgeFor(ev.score);
      if (badge && badge.at > announcedBadge && ev.score === badge.at) {
        announcedBadge = badge.at;
        audio.play('badge');
        view.flash = 0.55;
      }
    } else if (ev.type === 'crash') {
      audio.play('crash');
      view.shake = 1;
      view.flash = 1;
    }
  }
  sim.events.length = 0;
}

function updateAngle(vy, dt, active) {
  let target = 0;
  if (active) {
    if (vy <= 0) target = CONFIG.rotateUpDeg * (vy / CONFIG.flapImpulse);
    else {
      const t = Math.min(1, vy / CONFIG.terminalFall);
      target = CONFIG.rotateDownDeg * Math.pow(t, 1.4);
    }
  }
  const rad = target * Math.PI / 180;
  // Snap upward on a flap, then ease back down. The sharp flip is most of
  // what makes a flap feel like it did something.
  if (rad < view.angle) view.angle = rad;
  else view.angle += (rad - view.angle) * Math.min(1, CONFIG.rotateEase * dt);
}

function updateWing(dt, phase) {
  run.wingMs += dt * 1000;
  if (phase === STATE.DEAD || phase === STATE.DYING) {
    view.wingFrame = 2;
    return;
  }
  const idle = phase === 'attract' || phase === STATE.READY;
  const frameMs = idle ? CONFIG.wingHoldMs : CONFIG.wingFrameMs;
  view.wingFrame = WING_SEQUENCE[Math.floor(run.wingMs / frameMs) % WING_SEQUENCE.length];
}

function frame(nowMs) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.25, (nowMs - lastFrameMs) / 1000);
  lastFrameMs = nowMs;
  view.nowMs = nowMs;

  const sim = view.sim;
  let alpha = 0;

  if (sim) {
    const wasDead = sim.state === STATE.DEAD;
    const target = Math.floor((nowMs - run.startMs) / CONFIG.stepMs);
    let guard = 0;
    while (sim.tick < target && guard < CONFIG.maxCatchUpSteps) {
      step(sim);
      guard += 1;
    }
    if (guard >= CONFIG.maxCatchUpSteps) {
      // The tab was in the background. Rebase rather than spend the next
      // several seconds simulating a run nobody was playing.
      run.startMs = nowMs - sim.tick * CONFIG.stepMs;
    }
    alpha = Math.max(0, Math.min(1, (nowMs - run.startMs) / CONFIG.stepMs - sim.tick));
    drainEvents(sim);
    if (!wasDead && sim.state === STATE.DEAD) onDead();

    view.phase = sim.state;
    view.score = sim.score;
    view.scrollX = sim.prevScrollX + (sim.scrollX - sim.prevScrollX) * alpha;

    const bobbing = sim.state === STATE.READY;
    view.duckY = bobbing
      ? CONFIG.startY + Math.sin(nowMs / 1000 * 2 * Math.PI * CONFIG.bobHz) * CONFIG.bobAmp
      : sim.prevDuckY + (sim.duckY - sim.prevDuckY) * alpha;

    if (sim.state === STATE.READY) view.decorScroll += CONFIG.scrollSpeed * dt;
    else view.decorScroll = run.decorBase + view.scrollX;
    if (sim.state === STATE.READY) run.decorBase = view.decorScroll;

    updateAngle(sim.duckVy, dt, !bobbing);
  } else {
    view.phase = 'attract';
    view.scrollX = 0;
    view.decorScroll += CONFIG.scrollSpeed * dt;
    view.duckY = CONFIG.startY
      + Math.sin(nowMs / 1000 * 2 * Math.PI * CONFIG.bobHz) * CONFIG.bobAmp;
    updateAngle(0, dt, false);
  }

  updateWing(dt, view.phase);
  view.shake = Math.max(0, view.shake - dt * 1000 / CONFIG.shakeMs);
  view.flash = Math.max(0, view.flash - dt * 1000 / CONFIG.flashMs);

  renderer.render(view);
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

async function boot() {
  view.best = parseInt(readStore(CONFIG.bestKey, '0'), 10) || 0;
  view.muted = readStore(CONFIG.mutedKey, '1') !== '0';
  el.player.value = readStore(CONFIG.playerKey, '');
  await loadRoster();

  audio = createAudio(view.muted);
  try {
    atlas = await loadAtlas(BASE);
  } catch (err) {
    renderer = createRenderer(el.canvas, null);
    renderer.resize();
    renderer.drawLoading('Could not load the art. Reload the page.', true);
    return;
  }
  renderer = createRenderer(el.canvas, atlas);
  renderer.resize();
  bindInput();
  setStatus('Tap, click, Space or Up to flap. M mutes.', '');
  lastFrameMs = performance.now();
  requestAnimationFrame(frame);
}

boot();
