/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
import { createSim, queueInput, step, durationMs, ACTION, STATE } from './sim.mjs';
import { createAudio } from './audio.mjs';
import { createRenderer } from './render.mjs';

const API = '/sweeper/api/';
const RESTART_LOCKOUT_MS = 800;
const BEAT_MS = 5000;

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
};

const view = { sim: null, best: 0, alpha: 0, statusLines: [] };

const run = {
  startMs: 0,
  deadAtMs: 0,
  submitted: false,
  posted: false,
  session: '',
  lastBeatMs: 0,
  wasPlaying: false,
};

/** Turn is a stack, same reasoning as the other games' steering: releasing
 * one held key falls back to the other rather than stopping outright. */
const input = { steer: [], dir: ACTION.TURN_NEUTRAL, thrusting: false };

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
  setStatus('Arrows to turn, Up to thrust, Space to fire. M mutes.', '');
  prefetchSession();

  if (input.dir !== ACTION.TURN_NEUTRAL) queueInput(view.sim, 0, input.dir);
  if (input.thrusting) queueInput(view.sim, 0, ACTION.THRUST_ON);
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
    panelLines([{ text: 'NOTHING SWEPT' }, { text: 'NOTHING TO POST' }]);
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
        inputs: sim.inputs.slice(0, CONFIG.maxInputTrace),
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

    const lines = [{ text: 'POSTED AS ' + (data.player || player), color: '#c08bff' }];
    if (data.rank) lines.push({ text: 'ALL TIME RANK ' + data.rank });
    if (data.personal_best) lines.push({ text: 'NEW PERSONAL BEST', color: '#ffd23f' });
    lines.push({ text: 'PRESS FIRE TO RETRY', color: '#9aa4b2' });
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

function applySteer(nowMs) {
  const dir = input.steer.length
    ? input.steer[input.steer.length - 1]
    : ACTION.TURN_NEUTRAL;
  if (dir === input.dir) return;
  input.dir = dir;
  const sim = view.sim;
  if (!sim || sim.state === STATE.DEAD) return;
  queueInput(sim, tickForNow(nowMs), dir);
}

function pressSteer(dir, nowMs) {
  if (input.steer[input.steer.length - 1] === dir) return;
  const i = input.steer.indexOf(dir);
  if (i >= 0) input.steer.splice(i, 1);
  input.steer.push(dir);
  applySteer(nowMs);
}

function releaseSteer(dir, nowMs) {
  const i = input.steer.indexOf(dir);
  if (i < 0) return;
  input.steer.splice(i, 1);
  applySteer(nowMs);
}

function setThrust(on, nowMs) {
  if (input.thrusting === on) return;
  input.thrusting = on;
  const sim = view.sim;
  if (!sim || sim.state === STATE.DEAD) return;
  queueInput(sim, tickForNow(nowMs), on ? ACTION.THRUST_ON : ACTION.THRUST_OFF);
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

const KEY_LEFT = new Set(['ArrowLeft', 'KeyA']);
const KEY_RIGHT = new Set(['ArrowRight', 'KeyD']);
const KEY_THRUST = new Set(['ArrowUp', 'KeyW']);
const KEY_FIRE = new Set(['Space', 'Enter']);

function bindInput() {
  window.addEventListener('keydown', (ev) => {
    const tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
    if (ev.code === 'KeyM') { toggleMute(); return; }
    const known = KEY_LEFT.has(ev.code) || KEY_RIGHT.has(ev.code)
      || KEY_THRUST.has(ev.code) || KEY_FIRE.has(ev.code);
    if (!known) return;
    ev.preventDefault();
    audio.unlock();
    const now = performance.now();
    if (tryStart(now)) return;
    if (KEY_LEFT.has(ev.code)) pressSteer(ACTION.TURN_LEFT, now);
    else if (KEY_RIGHT.has(ev.code)) pressSteer(ACTION.TURN_RIGHT, now);
    else if (KEY_THRUST.has(ev.code)) setThrust(true, now);
    else if (KEY_FIRE.has(ev.code) && !ev.repeat) {
      const sim = view.sim;
      if (sim && sim.state !== STATE.DEAD) queueInput(sim, tickForNow(now), ACTION.FIRE);
    }
  });

  window.addEventListener('keyup', (ev) => {
    const now = performance.now();
    if (KEY_LEFT.has(ev.code)) releaseSteer(ACTION.TURN_LEFT, now);
    else if (KEY_RIGHT.has(ev.code)) releaseSteer(ACTION.TURN_RIGHT, now);
    else if (KEY_THRUST.has(ev.code)) setThrust(false, now);
  });

  el.canvas.addEventListener('touchstart', (ev) => {
    audio.unlock();
    const now = performance.now();
    if (tryStart(now)) { ev.preventDefault(); return; }
    setThrust(true, now);
    const sim = view.sim;
    if (sim && sim.state !== STATE.DEAD) queueInput(sim, tickForNow(now), ACTION.FIRE);
    ev.preventDefault();
  }, { passive: false });

  el.canvas.addEventListener('touchend', () => {
    setThrust(false, performance.now());
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
    { text: 'PATCH SWEEPER', color: '#c08bff', big: true },
    { text: 'PRESS ANY KEY TO START', blink: true },
    { text: 'ARROWS TO FLY, SPACE TO FIRE', color: '#9aa4b2' },
  ]);
  setStatus('Arrows to turn, Up to thrust, Space to fire. M mutes.', '');

  if (new URLSearchParams(location.search).has('debug')) {
    window.__sweeper = { view, run, CONFIG, STATE, ACTION, queueInput, step };
  }

  requestAnimationFrame(frame);
}

boot();
