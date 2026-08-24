/**
 * The game: input, the fixed-timestep loop, presentation state, and score
 * submission. All the rules live in sim.mjs and all the numbers live in
 * config.mjs, so this file is glue.
 */
import { CONFIG } from './config.mjs';
import {
  createSim, queueInput, step, durationMs, canFire, ACTION, STATE,
} from './sim.mjs';
import { createAudio } from './audio.mjs';
import { createRenderer, loadImage } from './render.mjs';

const BASE = '/patchaga/static/';
const API = '/patchaga/api/';
const RESTART_LOCKOUT_MS = 800; // stops the death keypress restarting the run
const BEAT_MS = 5000;           // matches BEAT_INTERVAL_MS in patchaga.py

const el = {
  canvas: document.getElementById('game'),
  player: document.getElementById('player'),
  post: document.getElementById('post'),
  status: document.getElementById('status'),
};

const view = {
  sim: null,
  best: 0,
  alpha: 0,
  dt: 0,
  nowMs: 0,
  shake: 0,
  flash: 0,
  muted: true,
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

/**
 * Held keys, kept here rather than read from the sim.
 *
 * ``steer`` is a stack rather than a pair of booleans so that holding left,
 * then also pressing right, then releasing right, goes back to left instead of
 * stopping. Anything else feels broken during the corrections that make up
 * most of the steering in this game.
 */
const input = {
  steer: [],       // ACTION.LEFT / ACTION.RIGHT, most recent last
  dir: ACTION.NEUTRAL,
  fireHeld: false,  // only so a pointer drag knows it is still down
  fireLatch: false, // one press waiting to become one patch
};

let renderer = null;
let audio = null;
let lastFrameMs = 0;
// A seed the server has handed over, waiting to be played. Fetched ahead of
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
 * Ask the server for a seed to play.
 *
 * The seed is picked there, not here, because a seed the client chooses is a
 * seed the client can shop around for: the rules in this folder can be imported
 * and searched offline for a wave order that happens to play itself. One
 * request ahead of time keeps the first keypress instant.
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
    setStatus('Getting a fresh wave...', '');
    panelLines([{ text: 'GETTING A WAVE...' }]);
    prefetchSession().then((got) => { if (got) startRun(); });
    return;
  }
  const issued = nextSession;
  nextSession = null;

  view.sim = createSim(issued.seed);
  view.popups.length = 0;
  run.session = issued.session;
  run.startMs = performance.now();
  run.submitted = false;
  run.posted = false;
  run.lastBeatMs = 0;
  run.wasPlaying = false;
  panelLines([]);
  setStatus('Arrows or A/D to steer. Space or W to fire. M mutes.', '');
  prefetchSession(); // have the next one ready before this one ends

  // Whatever is being held right now applies to the new run too, so a player
  // who never let go of the fire key does not have to press it again.
  if (input.dir !== ACTION.NEUTRAL) queueInput(view.sim, 0, input.dir);
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
        inputs: sim.inputs.slice(0, CONFIG.maxInputTrace),
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

// --------------------------------------------------------------------------
// Input
// --------------------------------------------------------------------------

function tickForNow(nowMs) {
  return Math.floor((nowMs - run.startMs) / CONFIG.stepMs);
}

/**
 * Recompute the steering direction from what is held and record any change.
 *
 * Only changes are recorded. Holding a key is one instruction no matter how
 * long it is held, which is what keeps the trace small and keeps the timing
 * statistics the server computes a measurement of the player rather than of
 * the browser's key repeat rate.
 */
function applySteer(nowMs) {
  const dir = input.steer.length
    ? input.steer[input.steer.length - 1]
    : ACTION.NEUTRAL;
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

/**
 * Start, or restart, when the player asks for one.
 *
 * The lockout exists because the keypress that killed you is usually still
 * going down when the run ends, and without it the game restarts before the
 * player has read the score.
 */
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
  view.muted = audio.toggle();
  writeStore(CONFIG.muteKey, view.muted ? '1' : '0');
}

const KEY_LEFT = new Set(['ArrowLeft', 'KeyA']);
const KEY_RIGHT = new Set(['ArrowRight', 'KeyD']);
const KEY_FIRE = new Set(['Space', 'ArrowUp', 'KeyW', 'Enter']);

function bindInput() {
  window.addEventListener('keydown', (ev) => {
    const tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
    const now = performance.now();

    if (KEY_LEFT.has(ev.code)) {
      ev.preventDefault();
      if (ev.repeat) return;
      audio.unlock();
      tryStart(now);
      pressSteer(ACTION.LEFT, now);
    } else if (KEY_RIGHT.has(ev.code)) {
      ev.preventDefault();
      if (ev.repeat) return;
      audio.unlock();
      tryStart(now);
      pressSteer(ACTION.RIGHT, now);
    } else if (KEY_FIRE.has(ev.code)) {
      ev.preventDefault();
      // The OS sends repeated keydowns for a held key. Letting those set the
      // latch would restore hold-to-fire through the back door, at whatever
      // rate the keyboard happens to be configured for.
      if (ev.repeat) return;
      audio.unlock();
      tryStart(now);
      input.fireHeld = true;
      input.fireLatch = true;
    } else if (ev.code === 'KeyM') {
      audio.unlock();
      toggleMute();
    }
  });

  window.addEventListener('keyup', (ev) => {
    const now = performance.now();
    if (KEY_LEFT.has(ev.code)) releaseSteer(ACTION.LEFT, now);
    else if (KEY_RIGHT.has(ev.code)) releaseSteer(ACTION.RIGHT, now);
    else if (KEY_FIRE.has(ev.code)) input.fireHeld = false;
  });

  // A key held when the tab loses focus never sends its keyup, and the duck
  // would steer into the wall forever. Let go of everything instead.
  window.addEventListener('blur', () => {
    input.steer.length = 0;
    input.fireHeld = false;
    applySteer(performance.now());
  });

  /**
   * Touch: the left half of the playfield steers left, the right half steers
   * right, and any touch fires.
   *
   * Dragging the duck to follow a finger would feel better but it is not an
   * option here: the steering edges would then be generated by the duck
   * arriving somewhere rather than by the player, which is exactly the
   * machine-regular timing the server looks for.
   */
  function pointAt(ev) {
    const rect = el.canvas.getBoundingClientRect();
    return (ev.clientX - rect.left) / rect.width;
  }

  el.canvas.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const now = performance.now();
    audio.unlock();
    if (renderer.hitsMute(ev.clientX, ev.clientY)) { toggleMute(); return; }
    tryStart(now);
    input.fireHeld = true;
    input.fireLatch = true;
    pressSteer(pointAt(ev) < 0.5 ? ACTION.LEFT : ACTION.RIGHT, now);
    try { el.canvas.setPointerCapture(ev.pointerId); } catch (err) { /* ignore */ }
  });

  el.canvas.addEventListener('pointermove', (ev) => {
    if (!input.fireHeld || !input.steer.length) return;
    pressSteer(pointAt(ev) < 0.5 ? ACTION.LEFT : ACTION.RIGHT, performance.now());
  });

  function endPointer() {
    const now = performance.now();
    input.fireHeld = false;
    input.steer.length = 0;
    applySteer(now);
  }
  el.canvas.addEventListener('pointerup', endPointer);
  el.canvas.addEventListener('pointercancel', endPointer);
  // Suppress the synthetic mouse event and double-tap zoom that follow a touch.
  el.canvas.addEventListener('touchstart', (ev) => ev.preventDefault(),
    { passive: false });

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
    kind: 'text',
    text,
    x: subX / CONFIG.unit,
    y: subY / CONFIG.unit,
    color,
    life: 850,
    max: 850,
  });
  if (view.popups.length > 24) view.popups.shift();
}

function burst(subX, subY, color) {
  view.popups.push({
    kind: 'burst',
    x: subX / CONFIG.unit,
    y: subY / CONFIG.unit,
    color,
    life: 320,
    max: 320,
  });
  if (view.popups.length > 24) view.popups.shift();
}

const POP_COLOR = { drone: '#8fd0ff', weevil: '#c08bff', rootkit: '#ff6b4a' };

function drainEvents(sim) {
  for (const ev of sim.events) {
    switch (ev.type) {
      case 'pop':
        audio.play('pop');
        burst(ev.x, ev.y, POP_COLOR[ev.kind] || '#8fd0ff');
        popup(String(ev.points), ev.x, ev.y, POP_COLOR[ev.kind] || '#8fd0ff');
        break;
      case 'merge':
        audio.play('merge');
        view.flash = 0.5;
        popup('+' + CONFIG.mergeBonus, sim.duck.x, CONFIG.duckY * CONFIG.unit,
          '#ffd23f');
        break;
      case 'perfect':
        audio.play('perfect');
        view.flash = 0.7;
        popup('PERFECT +' + CONFIG.sweepPerfect,
          CONFIG.width / 2 * CONFIG.unit, 300 * CONFIG.unit, '#4ecca3');
        break;
      case 'die':
        audio.play('die');
        view.shake = 7;
        view.flash = 0.6;
        break;
      case 'fork':
        audio.play('fork');
        view.shake = 5;
        popup('FORKED', sim.duck.x, CONFIG.duckY * CONFIG.unit, '#ff6b4a');
        break;
      case 'freed':
        audio.play('freed');
        view.flash = 0.35;
        break;
      case 'lostfork':
        audio.play('unmerge');
        break;
      case 'clear':
        audio.play('clear');
        view.flash = 0.45;
        break;
      case 'extralife':
        audio.play('extralife');
        popup('EXTRA DUCK', sim.duck.x, CONFIG.duckY * CONFIG.unit, '#4ecca3');
        break;
      default:
        // Everything left is a plain sound: fire, enter, dive, sweep, beam,
        // bugfire, unmerge, respawn, gameover.
        audio.play(ev.type);
        break;
    }
  }
  // The simulation never clears this, deliberately: it has no idea whether
  // anyone is looking at it. Left undrained the array grows for the whole run.
  sim.events.length = 0;
}

/**
 * Fire, if the player wants to and the duck can.
 *
 * One patch per press. ``fireLatch`` is set by the keydown edge and cleared
 * the moment a patch leaves, so holding the key does nothing after the first
 * shot and the player has to let go and press again. That is the genre's
 * behaviour and it is what playtesting asked for: an earlier build repeated
 * while held, and a held key emptying the magazine by itself reads as the game
 * firing on its own rather than on the player.
 *
 * The latch is deliberately not cleared when the shot cannot happen yet. A
 * press made while the cooldown is running, or while every patch slot is full,
 * is remembered and fires at the first tick that allows it. That is at most
 * 0.61s away and usually far less. Dropping it instead would mean a press that
 * the player definitely made produced nothing, which is the complaint this
 * whole function exists to answer.
 *
 * Gating on ``canFire`` means every press recorded in the trace produced a
 * patch, so the trace stays a record of the run rather than of the keyboard.
 *
 * The wave title is the one exception. A press there fires nothing, but it is
 * what promotes the wave from its title card to play, so it is not a no-op and
 * it does belong in the trace. Without this the only way past the title is to
 * steer, or to wait out the timeout.
 */
function wouldDoSomething(sim) {
  if (canFire(sim)) return true;
  return sim.state === STATE.READY && sim.stateTick >= CONFIG.readyTicks;
}

function maybeFire(sim) {
  if (!input.fireLatch) return;
  if (!wouldDoSomething(sim)) return;
  queueInput(sim, sim.tick, ACTION.FIRE);
  input.fireLatch = false;
}

function frame(nowMs) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.25, (nowMs - lastFrameMs) / 1000);
  lastFrameMs = nowMs;
  view.nowMs = nowMs;
  view.dt = dt;

  const sim = view.sim;
  if (sim) {
    const wasDead = sim.state === STATE.DEAD;
    maybeFire(sim);
    const target = Math.floor((nowMs - run.startMs) / CONFIG.stepMs);
    let guard = 0;
    while (sim.tick < target && guard < CONFIG.maxCatchUpSteps) {
      step(sim);
      guard += 1;
      // A buffered press has to be checked between ticks as well as between
      // frames. At 120 ticks a second a 60Hz frame covers two ticks, so a
      // press waiting on a cooldown that expires mid-frame would otherwise
      // wait for the next frame instead of the next tick it was allowed.
      maybeFire(sim);
    }
    if (guard >= CONFIG.maxCatchUpSteps) {
      // The tab was in the background. Rebase rather than spend the next
      // several seconds simulating a wave nobody was playing.
      run.startMs = nowMs - sim.tick * CONFIG.stepMs;
    }
    view.alpha = Math.max(0, Math.min(1,
      (nowMs - run.startMs) / CONFIG.stepMs - sim.tick));
    drainEvents(sim);
    pulse(nowMs);
    if (!wasDead && sim.state === STATE.DEAD) onDead();
  }

  view.shake = Math.max(0, view.shake - dt * 26);
  view.flash = Math.max(0, view.flash - dt * 2.6);
  for (let i = view.popups.length - 1; i >= 0; i--) {
    view.popups[i].life -= dt * 1000;
    if (view.popups[i].life <= 0) view.popups.splice(i, 1);
  }
  renderer.render(view);
}

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

async function boot() {
  view.best = parseInt(readStore(CONFIG.bestKey, '0'), 10) || 0;
  view.muted = readStore(CONFIG.muteKey, '1') !== '0';
  el.player.value = readStore(CONFIG.playerKey, '');

  audio = createAudio(view.muted);
  // The logo is the one piece of real artwork in the game, but it is only
  // decoration: if it fails to arrive the playfield loses a watermark rather
  // than the game refusing to start.
  const logo = await loadImage(BASE + 'logo.png');
  renderer = createRenderer(el.canvas, logo);
  renderer.resize();
  bindInput();
  prefetchSession();
  panelLines([
    { text: 'PATCHAGA', color: '#ffd23f', big: true },
    { text: 'FIRE TO START', blink: true },
    { text: 'ARROWS OR A/D TO STEER', color: '#9aa4b2' },
  ]);
  setStatus('Arrows or A/D to steer. Space or W to fire. M mutes.', '');
  lastFrameMs = performance.now();

  // A handle for the test harness, and only when it is asked for. Nothing is
  // hidden from the browser anyway: the rules are a static module and the seed
  // arrives over the wire, which is exactly why none of the anti-cheat depends
  // on the client keeping a secret.
  if (new URLSearchParams(location.search).has('debug')) {
    window.__patchaga = { view, run, input, CONFIG, STATE, ACTION, queueInput, step };
  }

  requestAnimationFrame(frame);
}

boot();
