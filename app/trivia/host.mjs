/**
 * The host screen: the big display, polled and re-rendered on a timer.
 *
 * There is no client-side game state here at all beyond what just arrived
 * from the last poll -- the server is the only authority on what phase the
 * game is in, because it is the one thing every player's phone also has to
 * agree with.
 */
import { createAudio } from './audio.mjs';

const API = '/trivia/api/';
const POLL_MS = 1000;

const el = {
  panel: document.getElementById('panel'),
};

let audio = null;
let lastPhase = null;

function letterClass(i) {
  return ['a', 'b', 'c', 'd'][i];
}

function joinUrl() {
  return location.origin + '/trivia/join';
}

function renderLobby(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `
    <p class="pill">WAITING FOR PLAYERS</p>
    <p class="muted" style="margin:0 0 4px">Join at</p>
    <p class="code">${location.host}/trivia/join</p>
    <p class="muted" style="margin:4px 0 18px">${state.playerCount} player${state.playerCount === 1 ? '' : 's'} joined</p>
  `;
  const list = document.createElement('div');
  list.className = 'leaderboard';
  for (const p of state.leaderboard) {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span class="name">${escapeHtml(p.name)}</span>`;
    list.appendChild(row);
  }
  wrap.appendChild(list);
  const btn = document.createElement('button');
  btn.className = 'primary';
  btn.textContent = 'Start game';
  btn.style.marginTop = '18px';
  btn.disabled = state.playerCount < 1;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const res = await fetch(API + 'start', { method: 'POST' });
    if (res.ok) audio.play('start');
    else btn.disabled = false;
  });
  wrap.appendChild(btn);
  el.panel.appendChild(wrap);
}

function renderQuestion(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  const frac = state.remaining / state.questionSeconds;
  wrap.innerHTML = `
    <p class="pill">ROUND ${state.round} OF ${state.rounds}</p>
    <p class="qtext">${escapeHtml(state.question)}</p>
    <div class="timerTrack"><div class="timerBar" style="width:${Math.max(0, frac * 100)}%"></div></div>
    <p class="muted">${state.answeredCount} of ${state.playerCount} answered</p>
  `;
  const grid = document.createElement('div');
  grid.className = 'choices';
  state.choices.forEach((text, i) => {
    const b = document.createElement('div');
    b.className = 'choice ' + letterClass(i);
    b.textContent = text;
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  el.panel.appendChild(wrap);
}

function renderReveal(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `<p class="pill">ROUND ${state.round} OF ${state.rounds} -- ANSWER</p>
    <p class="qtext">${escapeHtml(state.question)}</p>`;
  const grid = document.createElement('div');
  grid.className = 'choices';
  state.choices.forEach((text, i) => {
    const b = document.createElement('div');
    b.className = 'choice ' + letterClass(i) + (i === state.correct ? ' correctAnswer' : '');
    b.textContent = text;
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  const list = document.createElement('div');
  list.className = 'leaderboard';
  const heading = document.createElement('p');
  heading.className = 'muted';
  heading.style.marginTop = '16px';
  heading.textContent = 'Leaderboard';
  wrap.appendChild(heading);
  state.leaderboard.forEach((p, i) => {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span class="rank">${i + 1}</span><span class="name">${escapeHtml(p.name)}</span>`
      + `<span class="score">${p.score}</span>`;
    list.appendChild(row);
  });
  wrap.appendChild(list);
  el.panel.appendChild(wrap);
}

function renderFinal(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `<p class="pill">GAME OVER</p><p class="qtext">Final standings</p>`;
  const list = document.createElement('div');
  list.className = 'leaderboard';
  state.leaderboard.forEach((p, i) => {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span class="rank">${i + 1}</span><span class="name">${escapeHtml(p.name)}</span>`
      + `<span class="score">${p.score}</span>`;
    list.appendChild(row);
  });
  wrap.appendChild(list);
  const btn = document.createElement('button');
  btn.className = 'primary';
  btn.textContent = 'New game';
  btn.style.marginTop = '18px';
  btn.addEventListener('click', async () => {
    await fetch(API + 'reset', { method: 'POST' });
  });
  wrap.appendChild(btn);
  el.panel.appendChild(wrap);
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = String(text == null ? '' : text);
  return div.innerHTML;
}

async function poll() {
  try {
    const res = await fetch(API + 'state');
    const state = await res.json();
    if (state.phase !== lastPhase) {
      if (state.phase === 'reveal') audio.play('correct');
      lastPhase = state.phase;
    }
    if (state.phase === 'lobby') renderLobby(state);
    else if (state.phase === 'question') renderQuestion(state);
    else if (state.phase === 'reveal') renderReveal(state);
    else if (state.phase === 'final') renderFinal(state);
  } catch (err) {
    // A dropped poll is survivable; the next one tries again.
  }
  setTimeout(poll, POLL_MS);
}

audio = createAudio(true);
poll();
