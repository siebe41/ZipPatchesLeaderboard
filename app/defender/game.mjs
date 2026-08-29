/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
import { createSim, queueInput, step, durationMs, STATE } from './sim.mjs';
import { createAudio } from './audio.mjs';
import { createRenderer } from './render.mjs';

const API = '/defender/api/';
const RESTART_LOCKOUT_MS = 800;
const BEAT_MS = 5000;

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
};

const view = { sim: null, best: 0, alpha: 0, statusLines: [], aimX: null, aimY: null };

const run = {
  startMs: 0,
  deadAtMs: 0,
  submitted: false,
  posted: false,
  session: '',
  lastBeatMs: 0,
  wasPlaying: false,
};

let renderer = null;
let audio = null;
let nextSession = null;
let sessionInFlight = null;

function readStore(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (err) {
    return fallback;
  }
}

function writeStore(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (err) { /* ignore */ }
}

function setStatus(text, kind) {
  el.status.textContent = text;
  el.status.className = 'status' + (kind ? ' ' + kind : '');
}

function panelLines(lines) {
  view.statusLines = lines;
}

/** Client coordinates -> the canvas's own WIDTH x groundY space, the same
 * space fire() clamps into on the sim side. */
function pointerToWorld(ev) {
  const rect = el.canvas.getBoundingClientRect();
  const point = ev.changedTouches ? ev.changedTouches[0] : ev;
  const scaleX = CONFIG.width / rect.width;
  const scaleY = (CONFIG.height + CONFIG.hudTop) / rect.height;
  const x = (point.clientX - rect.left) * scaleX;
  const y = (point.clientY - rect.top) * scaleY - CONFIG.hudTop;
  return { x, y };
}

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

function startRun() {
  if (!nextSession) {
    setStatus('Getting a fresh rollout...', '');
    panelLines([{ text: 'GETTING A ROLLOUT...' }]);
    prefetchSession().then((got) => { if (got) startRun(); });
    return;
  }
  const issued = nextSession;
  nextSession = null;

  view.sim = createSim(issued.seed);
  run.session = issued.session;
  run.startMs = performance.now();
  run.submitted = false;
  run.posted = false;
  run.lastBeatMs = 0;
  run.wasPlaying = false;
  panelLines([]);
  setStatus('Click or tap to intercept. M mutes.', '');
  prefetchSession();
}

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
    sendBeat(sim.tick);
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
  if (sim.score > view.best) {
    view.best = sim.score;
    writeStore(CONFIG.bestKey, view.best);
  }
  if (run.wasPlaying) sendBeat(sim.tick);
  audio.play('gameover');
  submitRun();
}

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
    panelLines([{ text: 'NOTHING INTERCEPTED' }, { text: 'NOTHING TO POST' }]);
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
        inputs: sim.inputs.slice(0, CONFIG.maxShots * 3),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
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

    const lines = [{ text: 'POSTED AS ' + (data.player || player), color: '#8fd0ff' }];
    if (data.rank) lines.push({ text: 'ALL TIME RANK ' + data.rank });
    if (data.personal_best) lines.push({ text: 'NEW PERSONAL BEST', color: '#ffd23f' });
    lines.push({ text: 'CLICK TO RETRY', color: '#9aa4b2' });
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

function tickForNow(nowMs) {
  return Math.floor((nowMs - run.startMs) / CONFIG.stepMs);
}

function tryStart(nowMs) {
  const sim = view.sim;
  if (!sim) { startRun(); return true; }
  if (sim.state === STATE.DEAD && nowMs - run.deadAtMs > RESTART_LOCKOUT_MS) {
    startRun();
    return true;
  }
  return false;
}

function toggleMute() {
  const muted = audio.toggle();
  writeStore(CONFIG.muteKey, muted ? '1' : '0');
}

function bindInput() {
  el.canvas.addEventListener('mousemove', (ev) => {
    const w = pointerToWorld(ev);
    view.aimX = w.x;
    view.aimY = w.y;
  });

  el.canvas.addEventListener('click', (ev) => {
    audio.unlock();
    const now = performance.now();
    const w = pointerToWorld(ev);
    if (tryStart(now)) return;
    const sim = view.sim;
    if (sim && sim.state !== STATE.DEAD) queueInput(sim, tickForNow(now), w.x, w.y);
  });

  el.canvas.addEventListener('touchstart', (ev) => {
    audio.unlock();
    const now = performance.now();
    const w = pointerToWorld(ev);
    view.aimX = w.x;
    view.aimY = w.y;
    if (tryStart(now)) { ev.preventDefault(); return; }
    const sim = view.sim;
    if (sim && sim.state !== STATE.DEAD) queueInput(sim, tickForNow(now), w.x, w.y);
    ev.preventDefault();
  }, { passive: false });

  window.addEventListener('keydown', (ev) => {
    if (ev.code === 'KeyM') toggleMute();
  });

  el.post.addEventListener('click', () => submitRun());
  el.player.addEventListener('change', () => {
    writeStore(CONFIG.playerKey, el.player.value.trim());
  });
  window.addEventListener('resize', () => renderer.resize());
}

function drainEvents(sim) {
  for (const ev of sim.events) audio.play(ev.type);
  sim.events.length = 0;
}

function frame(nowMs) {
  requestAnimationFrame(frame);

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
      run.startMs = nowMs - sim.tick * CONFIG.stepMs;
    }
    view.alpha = Math.max(0, Math.min(1,
      (nowMs - run.startMs) / CONFIG.stepMs - sim.tick));
    drainEvents(sim);
    pulse(nowMs);
    if (!wasDead && sim.state === STATE.DEAD) onDead();
  }

  renderer.render(view);
}

async function boot() {
  view.best = parseInt(readStore(CONFIG.bestKey, '0'), 10) || 0;
  el.player.value = readStore(CONFIG.playerKey, '');

  audio = createAudio(readStore(CONFIG.muteKey, '1') !== '0');
  renderer = createRenderer(el.canvas);
  renderer.resize();
  bindInput();
  prefetchSession();
  panelLines([
    { text: 'PATCHDEFENDER', color: '#8fd0ff', big: true },
    { text: 'CLICK OR TAP TO START', blink: true },
    { text: 'INTERCEPT BEFORE THEY LAND', color: '#9aa4b2' },
  ]);
  setStatus('Click or tap to intercept. M mutes.', '');

  if (new URLSearchParams(location.search).has('debug')) {
    window.__defender = { view, run, CONFIG, STATE, queueInput, step };
  }

  requestAnimationFrame(frame);
}

boot();
