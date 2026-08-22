/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
import { UP, LEFT, DOWN, RIGHT } from './maze.mjs';
import {
  createSim, queueTurn, step, durationMs, badgesFor, STATE,
} from './sim.mjs';
import { createAudio } from './audio.mjs';
import { createRenderer, loadImage } from './render.mjs';

const BASE = '/patchman/static/';
const API = '/patchman/api/';
const RESTART_LOCKOUT_MS = 800; // stops the death keypress restarting the run
const BEAT_MS = 5000;           // matches BEAT_INTERVAL_MS in patchman.py

const KEYS = {
  ArrowUp: UP, ArrowLeft: LEFT, ArrowDown: DOWN, ArrowRight: RIGHT,
  KeyW: UP, KeyA: LEFT, KeyS: DOWN, KeyD: RIGHT,
};

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
};

const view = {
  sim: null,
  score: 0,
  best: 0,
  alpha: 0,
  shake: 0,
  flash: 0,
  muted: true,
  nowMs: 0,
  badge: null,
  statusLines: [],
  popups: [],
};

const run = {
  startMs: 0,
  deadAtMs: 0,
  submitted: false,
  posted: false,   // a run that reached the board must never reach it twice
  session: '',     // the server's handle for this run
  lastBeatMs: 0,
  wasPlaying: false,
};

let renderer = null;
let audio = null;
let lastFrameMs = 0;
let announcedBadge = 0;
// A board the server has handed over, waiting to be played. Fetched ahead of
// time so that the first keypress still starts instantly.
let nextSession = null;
let sessionInFlight = null;

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

/**
 * Ask the server for a board to play.
 *
 * The seed is picked there, not here, because a seed the client chooses is a
 * seed the client can shop around for: the rules in this folder can be imported
 * and searched offline for a board that happens to play itself. One request
 * ahead of time keeps the first keypress instant.
 */
function prefetchSession() {
  if (nextSession || sessionInFlight) return sessionInFlight;
  sessionInFlight = fetch(API + 'session', { method: 'POST' })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      nextSession = (data && data.seed) ? data : null;
      sessionInFlight = null;
      return nextSession;
    })
    .catch(() => { sessionInFlight = null; return null; });
  return sessionInFlight;
}

function startRun(firstDir) {
  if (!nextSession) {
    // Nothing in hand. Ask, and start as soon as one arrives.
    setStatus('Getting a fresh board...', '');
    panelLines([{ text: 'GETTING A BOARD...' }]);
    prefetchSession().then((got) => { if (got) startRun(firstDir); });
    return;
  }
  const issued = nextSession;
  nextSession = null;

  view.sim = createSim(issued.seed);
  view.score = 0;
  view.badge = null;
  view.popups.length = 0;
  run.session = issued.session;
  run.startMs = performance.now();
  run.submitted = false;
  run.posted = false;
  run.lastBeatMs = 0;
  run.wasPlaying = false;
  announcedBadge = 0;
  panelLines([]);
  setStatus('Arrows or WASD to move. M mutes.', '');
  prefetchSession(); // have the next one ready before this one ends

  if (firstDir !== undefined) queueTurn(view.sim, 0, firstDir);
}

/**
 * Tell the server the run is still going, and how far it has got.
 *
 * Cheap and frequent. Comparing how far the simulation advanced against how
 * much of the server's own time passed is what stops a run being computed in
 * milliseconds and handed in as if it had been played.
 */
function sendBeat(tick) {
  if (!run.session) return;
  fetch(API + 'beat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: run.session, tick }),
    keepalive: true,
  }).catch(() => { /* a dropped beat is survivable, the server allows for it */ });
}

function pulse(nowMs) {
  const sim = view.sim;
  if (!sim || !run.session) return;
  const playing = sim.state === STATE.PLAYING;
  if (playing && !run.wasPlaying) {
    run.wasPlaying = true;
    run.lastBeatMs = nowMs;
    sendBeat(sim.tick); // marks where the clock started
    return;
  }
  if (playing && nowMs - run.lastBeatMs >= BEAT_MS) {
    run.lastBeatMs = nowMs;
    sendBeat(sim.tick);
  }
}

function onDead() {
  const sim = view.sim;
  run.deadAtMs = performance.now();
  view.badge = badgesFor(sim.score)[0] || null;
  if (sim.score > view.best) {
    view.best = sim.score;
    writeStore(CONFIG.bestKey, view.best);
  }
  if (run.wasPlaying) sendBeat(sim.tick); // marks where the clock stopped
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
  if (sim.score <= 0) {
    panelLines([{ text: 'NOTHING PATCHED' }, { text: 'NOTHING TO POST' }]);
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
        session: run.session,
        player,
        score: sim.score,
        duration_ms: durationMs(sim),
        turns: sim.turns.slice(0, CONFIG.maxInputTrace),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // The session is spent either way, so retrying this run cannot work.
      run.posted = true;
      const msg = data.error || ('Submission failed (' + res.status + ')');
      panelLines([{ text: 'NOT POSTED', color: '#e94560' }]);
      setStatus(msg, 'bad');
      return;
    }
    writeStore(CONFIG.playerKey, data.player || player);
    run.posted = true;
    el.player.value = data.player || player;

    if (data.counted === false) {
      panelLines([{ text: 'NOT COUNTED', color: '#e94560' }]);
      setStatus(data.reason || 'That run could not be verified.', 'bad');
      return;
    }

    const lines = [{ text: 'POSTED AS ' + (data.player || player), color: '#4ecca3' }];
    if (data.rank) lines.push({ text: 'ALL TIME RANK ' + data.rank });
    if (data.personal_best) lines.push({ text: 'NEW PERSONAL BEST', color: '#ffd23f' });
    lines.push({ text: 'PRESS A DIRECTION TO RETRY', color: '#9aa4b2' });
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

function steer(dir, nowMs) {
  const sim = view.sim;
  if (!sim) { startRun(dir); return; }
  if (sim.state === STATE.DEAD) {
    if (nowMs - run.deadAtMs > RESTART_LOCKOUT_MS) startRun(dir);
    return;
  }
  queueTurn(sim, tickForNow(nowMs), dir);
}

function toggleMute() {
  view.muted = audio.toggle();
  writeStore(CONFIG.mutedKey, view.muted ? '1' : '0');
}

/**
 * Touch steering: the direction is whichever axis the tap is furthest along
 * from the middle of the board. A swipe would be more natural but needs a
 * gesture threshold, and a maze game is played in short corrections where a
 * threshold reads as the game ignoring you.
 */
function dirFromPoint(clientX, clientY) {
  const rect = el.canvas.getBoundingClientRect();
  const dx = (clientX - rect.left) / rect.width - 0.5;
  const dy = (clientY - rect.top) / rect.height - 0.5;
  if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? LEFT : RIGHT;
  return dy < 0 ? UP : DOWN;
}

function bindInput() {
  el.canvas.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    audio.unlock();
    if (renderer.hitsMute(ev.clientX, ev.clientY)) { toggleMute(); return; }
    steer(dirFromPoint(ev.clientX, ev.clientY), performance.now());
  });
  // Suppress the synthetic mouse event and double-tap zoom that follow a touch.
  el.canvas.addEventListener('touchstart', (ev) => ev.preventDefault(),
    { passive: false });

  window.addEventListener('keydown', (ev) => {
    const tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
    const dir = KEYS[ev.code];
    if (dir !== undefined) {
      ev.preventDefault();
      // Holding a key is one instruction, not one per repeat. The simulation
      // drops repeats too, but keeping them out of the trace keeps the trace
      // small and the timing statistics honest.
      if (ev.repeat) return;
      audio.unlock();
      steer(dir, performance.now());
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
      setStatus('That run has already been sent.', 'good');
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

function popup(text, subX, subY, color) {
  view.popups.push({
    text,
    x: subX / CONFIG.cell * CONFIG.tile,
    y: CONFIG.mazeTop + (subY / CONFIG.cell) * CONFIG.tile,
    color,
    at: view.nowMs,
    life: 900,
  });
  if (view.popups.length > 12) view.popups.shift();
}

function drainEvents(sim) {
  for (const ev of sim.events) {
    if (ev.type === 'patch') {
      audio.play('patch');
    } else if (ev.type === 'logo') {
      audio.play('logo');
    } else if (ev.type === 'patched') {
      audio.play('patched');
      popup(String(ev.points), ev.x, ev.y, '#8fd0ff');
    } else if (ev.type === 'bonus') {
      audio.play('bonus');
      popup(String(ev.points),
        (CONFIG.bonusTile[0] + 0.5) * CONFIG.cell,
        (CONFIG.bonusTile[1] + 0.5) * CONFIG.cell, '#ffd23f');
      view.flash = 0.3;
    } else if (ev.type === 'bonusUp') {
      audio.play('bonus');
    } else if (ev.type === 'cleared') {
      audio.play('level');
      view.flash = 0.5;
    } else if (ev.type === 'breach') {
      audio.play('breach');
      view.shake = 1;
      view.flash = 0.7;
    } else if (ev.type === 'over') {
      audio.play('over');
    }
  }
  sim.events.length = 0;

  // Badges are announced as they are crossed, not at the end, because the
  // point of them is to be noticed mid-run.
  const badge = badgesFor(sim.score)[0];
  if (badge && badge.at > announcedBadge) {
    announcedBadge = badge.at;
    audio.play('badge');
    view.flash = 0.55;
    popup(badge.label, sim.pac.x, sim.pac.y - CONFIG.cell, '#ffd23f');
  }
}

function frame(nowMs) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.25, (nowMs - lastFrameMs) / 1000);
  lastFrameMs = nowMs;
  view.nowMs = nowMs;

  const sim = view.sim;
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
      // several seconds simulating a board nobody was playing.
      run.startMs = nowMs - sim.tick * CONFIG.stepMs;
    }
    view.alpha = Math.max(0, Math.min(1,
      (nowMs - run.startMs) / CONFIG.stepMs - sim.tick));
    drainEvents(sim);
    pulse(nowMs);
    if (!wasDead && sim.state === STATE.DEAD) onDead();
    view.score = sim.score;
  }

  view.shake = Math.max(0, view.shake - dt * 3.2);
  view.flash = Math.max(0, view.flash - dt * 2.6);
  renderer.render(view);
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

async function boot() {
  view.best = parseInt(readStore(CONFIG.bestKey, '0'), 10) || 0;
  view.muted = readStore(CONFIG.mutedKey, '1') !== '0';
  el.player.value = readStore(CONFIG.playerKey, '');

  audio = createAudio(view.muted);
  // The logo is the one piece of real artwork in the game, but it is only
  // decoration: if it fails to arrive the pellets fall back to plain discs
  // rather than the game refusing to start.
  const logo = await loadImage(BASE + 'logo.png');
  renderer = createRenderer(el.canvas, logo);
  renderer.resize();
  bindInput();
  prefetchSession();
  setStatus('Arrows or WASD to move. M mutes.', '');
  lastFrameMs = performance.now();

  // A handle for the test harness, and only when it is asked for. Nothing is
  // hidden from the browser anyway: the rules are a static module and the seed
  // arrives over the wire, which is exactly why none of the anti-cheat depends
  // on the client keeping a secret.
  if (new URLSearchParams(location.search).has('debug')) {
    window.__patchman = { view, run, CONFIG, STATE, queueTurn, step };
  }

  requestAnimationFrame(frame);
}

boot();
