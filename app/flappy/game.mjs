/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
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
const BEAT_MS = 5000; // matches BEAT_INTERVAL_MS in flappy.py

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
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
  session: '', // the server's handle for this run
  lastBeatMs: 0,
  wasPlaying: false,
};

let atlas = null;
let renderer = null;
let audio = null;
let lastFrameMs = 0;
let announcedBadge = 0;
// A world the server has handed over, waiting to be played. Fetched ahead of
// time so that tapping to start still feels instant.
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
 * Ask the server for a world to play.
 *
 * The seed used to be picked here. It is picked there now, because a seed the
 * client chooses is a seed the client can shop around for: the physics in this
 * folder can be imported and searched for a perfect run offline. One request
 * ahead of time keeps the first tap instant.
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

function startRun() {
  if (!nextSession) {
    // Nothing in hand. Ask, and start as soon as one arrives.
    setStatus('Getting a fresh run...', '');
    panelLines([{ text: 'GETTING A RUN...' }]);
    prefetchSession().then((got) => { if (got) startRun(); });
    return;
  }
  const issued = nextSession;
  nextSession = null;

  view.sim = createSim(issued.seed);
  run.session = issued.session;
  run.startMs = performance.now();
  run.decorBase = view.decorScroll;
  run.submitted = false;
  run.posted = false;
  run.wingMs = 0;
  run.lastBeatMs = 0;
  run.wasPlaying = false;
  announcedBadge = 0;
  view.badge = null;
  view.score = 0;
  view.angle = 0;
  panelLines([]);
  setStatus('Tap, click, Space or Up to flap. M mutes.', '');
  prefetchSession(); // have the next one ready before this one ends
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
        session: run.session,
        player,
        score: sim.score,
        duration_ms: durationMs(sim),
        flaps: sim.flaps.slice(0, CONFIG.maxFlapTrace),
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
    pulse(nowMs);
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
  prefetchSession();
  setStatus('Tap, click, Space or Up to flap. M mutes.', '');
  lastFrameMs = performance.now();

  // A handle for the test harness, and only when it is asked for. Nothing is
  // hidden from the browser anyway: the physics are a static module and the
  // seed arrives over the wire, which is exactly why none of the anti-cheat
  // depends on the client keeping a secret.
  if (new URLSearchParams(location.search).has('debug')) {
    window.__flappy = { view, run, CONFIG, STATE, queueFlap, step };
  }

  requestAnimationFrame(frame);
}

boot();
