/**
 * The player's own phone: join, answer, see how you did.
 *
 * Like the host screen, this holds no game state of its own beyond the
 * player's own join token -- everything about what round it is and what the
 * question was comes from the last poll, because the server is the only
 * thing every device in the room has to agree with.
 */
import { createAudio } from './audio.mjs';

const API = '/trivia/api/';
const POLL_MS = 1000;
const TOKEN_KEY = 'trivia.token';
const NAME_KEY = 'trivia.name';

const el = { panel: document.getElementById('panel') };

let audio = null;
let token = readStore(TOKEN_KEY, '');
let lastPhase = null;
let answeredThisPoll = false;

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

function letterClass(i) {
  return ['a', 'b', 'c', 'd'][i];
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = String(text == null ? '' : text);
  return div.innerHTML;
}

function renderJoinForm(prefillStatus) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `
    <p class="pill">JOIN THE ROUND</p>
    <p class="qtext">What's your name?</p>
  `;
  const field = document.createElement('div');
  field.className = 'field';
  field.innerHTML = `<label for="name">Player name</label>`;
  const input = document.createElement('input');
  input.id = 'name';
  input.maxLength = 40;
  input.autocomplete = 'off';
  input.value = readStore(NAME_KEY, '');
  input.placeholder = 'Your name';
  field.appendChild(input);
  wrap.appendChild(field);

  const status = document.createElement('p');
  status.className = 'status' + (prefillStatus ? ' bad' : '');
  status.textContent = prefillStatus || '';
  wrap.appendChild(status);

  const btn = document.createElement('button');
  btn.className = 'primary';
  btn.textContent = 'Join';
  btn.addEventListener('click', async () => {
    const name = input.value.trim();
    if (!name) { status.textContent = 'Enter a name first.'; status.className = 'status bad'; return; }
    audio.unlock();
    btn.disabled = true;
    try {
      const res = await fetch(API + 'join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) {
        status.textContent = data.error || 'Could not join.';
        status.className = 'status bad';
        btn.disabled = false;
        return;
      }
      token = data.token;
      writeStore(TOKEN_KEY, token);
      writeStore(NAME_KEY, name);
    } catch (err) {
      status.textContent = 'Could not reach the game. Try again.';
      status.className = 'status bad';
      btn.disabled = false;
    }
  });
  wrap.appendChild(btn);
  el.panel.appendChild(wrap);
}

function renderWaiting(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  const name = state.you ? state.you.name : readStore(NAME_KEY, '');
  wrap.innerHTML = `
    <p class="pill">YOU'RE IN</p>
    <p class="qtext">Welcome, ${escapeHtml(name)}!</p>
    <p class="muted">Waiting for the host to start the game...</p>
    <p class="muted">${state.playerCount} player${state.playerCount === 1 ? '' : 's'} joined</p>
  `;
  el.panel.appendChild(wrap);
}

function renderQuestion(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `<p class="pill">ROUND ${state.round} OF ${state.rounds}</p>
    <p class="qtext">${escapeHtml(state.question)}</p>`;

  if (state.youAnswered) {
    const status = document.createElement('p');
    status.className = 'status good';
    status.textContent = 'Locked in! Waiting for the round to end...';
    wrap.appendChild(status);
    el.panel.appendChild(wrap);
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'choices';
  state.choices.forEach((text, i) => {
    const b = document.createElement('button');
    b.className = 'choice ' + letterClass(i);
    b.textContent = text;
    b.addEventListener('click', async () => {
      audio.unlock();
      grid.querySelectorAll('button').forEach((n) => { n.disabled = true; });
      audio.play('lockin');
      try {
        await fetch(API + 'answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, choice: i }),
        });
      } catch (err) { /* the next poll will show the real state either way */ }
    });
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  el.panel.appendChild(wrap);
}

function renderReveal(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  wrap.innerHTML = `<p class="pill">ROUND ${state.round} OF ${state.rounds}</p>
    <p class="qtext">${escapeHtml(state.question)}</p>`;

  const grid = document.createElement('div');
  grid.className = 'choices';
  state.choices.forEach((text, i) => {
    const b = document.createElement('div');
    let cls = 'choice ' + letterClass(i);
    if (i === state.correct) cls += ' correctAnswer';
    else if (state.yourAnswer && state.yourAnswer.choice === i) cls += ' wrongAnswer';
    b.className = cls;
    b.textContent = text;
    grid.appendChild(b);
  });
  wrap.appendChild(grid);

  const result = document.createElement('p');
  const ans = state.yourAnswer;
  if (ans && ans.correct) {
    result.className = 'status good';
    result.textContent = 'Correct! +' + ans.points + ' points';
  } else if (ans) {
    result.className = 'status bad';
    result.textContent = 'Not quite. The answer was highlighted above.';
  } else {
    result.className = 'status bad';
    result.textContent = "Time's up -- no answer recorded.";
  }
  wrap.appendChild(result);

  if (state.you) {
    const score = document.createElement('p');
    score.className = 'muted';
    score.textContent = 'Your score: ' + state.you.score;
    wrap.appendChild(score);
  }
  el.panel.appendChild(wrap);
}

function renderFinal(state) {
  el.panel.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'panel';
  const rank = state.you
    ? state.leaderboard.findIndex((p) => p.name === state.you.name && p.score === state.you.score) + 1
    : 0;
  wrap.innerHTML = `
    <p class="pill">GAME OVER</p>
    <p class="qtext">${state.you ? 'Nice run, ' + escapeHtml(state.you.name) + '!' : 'Thanks for playing!'}</p>
    ${state.you ? `<p class="muted">Final score: ${state.you.score}${rank ? ' -- placed ' + rank + ' of ' + state.leaderboard.length : ''}</p>` : ''}
    <p class="muted" style="margin-top:14px">Ask the host to start a new game to play again.</p>
  `;
  el.panel.appendChild(wrap);
}

async function poll() {
  try {
    const res = await fetch(API + 'state?token=' + encodeURIComponent(token));
    const state = await res.json();

    if (state.phase === 'lobby' && !state.you) {
      // Either we never joined, or the host started a fresh game.
      renderJoinForm(token ? 'That game has ended. Join the new one below.' : '');
      token = '';
      writeStore(TOKEN_KEY, '');
    } else if (state.phase === 'lobby') {
      renderWaiting(state);
    } else if (state.phase === 'question') {
      renderQuestion(state);
    } else if (state.phase === 'reveal') {
      if (lastPhase !== 'reveal') {
        audio.play(state.yourAnswer && state.yourAnswer.correct ? 'correct' : 'wrong');
      }
      renderReveal(state);
    } else if (state.phase === 'final') {
      if (lastPhase !== 'final') audio.play('final');
      renderFinal(state);
    }
    lastPhase = state.phase;
  } catch (err) {
    // A dropped poll is survivable; the next one tries again.
  }
  setTimeout(poll, POLL_MS);
}

audio = createAudio(true);
if (!token) renderJoinForm('');
poll();
