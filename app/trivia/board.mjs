/**
 * The leaderboard. Two lists over the same table: the best individual
 * scores ever posted, and the most recent games played -- there is no
 * season/today split here the way the score-attack games have one, because
 * a trivia result belongs to a specific live game, not a run anyone can
 * repeat on demand.
 */
const API = '/trivia/api/';

const el = {
  top: document.getElementById('top'),
  topEmpty: document.getElementById('topEmpty'),
  recent: document.getElementById('recent'),
  recentEmpty: document.getElementById('recentEmpty'),
};

function cell(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function fmtWhen(stamp) {
  const when = new Date(stamp);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleDateString([], { month: 'short', day: 'numeric' })
    + ' ' + when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function topRow(row, rank) {
  const tr = document.createElement('tr');
  if (rank <= 3) tr.className = 'top' + rank;
  tr.appendChild(cell('td', rank, 'rank'));
  tr.appendChild(cell('td', row.player, 'player'));
  tr.appendChild(cell('td', row.score, 'score'));
  tr.appendChild(cell('td', row.correct + '/' + row.rounds, 'num'));
  tr.appendChild(cell('td', fmtWhen(row.created_at), 'when'));
  return tr;
}

function recentRow(row) {
  const tr = document.createElement('tr');
  if (row.rank === 1) tr.className = 'top1';
  tr.appendChild(cell('td', '#' + row.rank + ' of ' + row.players, 'rank'));
  tr.appendChild(cell('td', row.player, 'player'));
  tr.appendChild(cell('td', row.score, 'score'));
  tr.appendChild(cell('td', row.correct + '/' + row.rounds, 'num'));
  tr.appendChild(cell('td', fmtWhen(row.created_at), 'when'));
  return tr;
}

async function load() {
  let data;
  try {
    const res = await fetch(API + 'board?limit=15');
    data = await res.json();
  } catch (err) {
    el.topEmpty.textContent = 'Could not load the board. Try again in a moment.';
    el.topEmpty.hidden = false;
    el.recentEmpty.hidden = true;
    return;
  }

  el.top.innerHTML = '';
  (data.top || []).forEach((row, i) => el.top.appendChild(topRow(row, i + 1)));
  el.topEmpty.hidden = (data.top || []).length > 0;

  el.recent.innerHTML = '';
  (data.recent || []).forEach((row) => el.recent.appendChild(recentRow(row)));
  el.recentEmpty.hidden = (data.recent || []).length > 0;
}

load();
