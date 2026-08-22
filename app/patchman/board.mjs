/**
 * The board page. Four views over one table, all derived at query time.
 *
 * Everything is written with textContent rather than innerHTML, so a player
 * name can never turn into markup.
 */
import { CONFIG } from './config.mjs';

const API = '/patchman/api/';

const VIEWS = [
  {
    id: 'alltime',
    label: 'All time',
    blurb: 'Best single run each player has ever posted.',
    scoreHead: 'Best run',
  },
  {
    id: 'season',
    label: 'This season',
    blurb: 'Best single run this calendar month. A season is worked out from '
      + 'when a run happened, so nothing rolls over and nothing resets.',
    scoreHead: 'Best run',
  },
  {
    id: 'today',
    label: 'Today',
    blurb: 'Best single run since midnight.',
    scoreHead: 'Best run',
  },
  {
    id: 'volume',
    label: 'Most patched',
    blurb: 'Every point scored across every run. Persistence, not peak.',
    scoreHead: 'Total score',
  },
];

const el = {
  tabs: document.getElementById('tabs'),
  blurb: document.getElementById('blurb'),
  head: document.getElementById('head'),
  rows: document.getElementById('rows'),
  empty: document.getElementById('empty'),
  you: document.getElementById('you'),
  stats: document.getElementById('stats'),
  recent: document.getElementById('recent'),
  fame: document.getElementById('fame'),
  fameRows: document.getElementById('fameRows'),
};

let current = 'alltime';
let player = '';

function readStore(key) {
  try { return localStorage.getItem(key) || ''; } catch (err) { return ''; }
}

function cell(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function nameKey(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function fmtWhen(stamp, view) {
  const when = new Date(stamp);
  if (Number.isNaN(when.getTime())) return '';
  if (view === 'today') {
    return when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  return when.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function fmtDuration(ms) {
  if (!ms) return '';
  const s = ms / 1000;
  return s < 60 ? s.toFixed(1) + 's'
    : Math.floor(s / 60) + 'm ' + String(Math.round(s % 60)).padStart(2, '0') + 's';
}

function buildTabs() {
  el.tabs.innerHTML = '';
  for (const view of VIEWS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = view.label;
    button.setAttribute('aria-current', String(view.id === current));
    button.addEventListener('click', () => {
      current = view.id;
      const url = new URL(location.href);
      url.searchParams.set('view', current);
      history.replaceState(null, '', url);
      load();
    });
    el.tabs.appendChild(button);
  }
}

function renderHead(view) {
  el.head.innerHTML = '';
  el.head.appendChild(cell('th', '#', 'rank'));
  el.head.appendChild(cell('th', 'Player'));
  el.head.appendChild(cell('th', view.scoreHead, 'num'));
  if (view.id === 'volume') el.head.appendChild(cell('th', 'Runs', 'num'));
  else el.head.appendChild(cell('th', 'Level', 'num'));
  el.head.appendChild(cell('th', view.id === 'volume' ? 'Last run' : 'Set', 'when num'));
}

function renderRow(row, view, key, pinned) {
  const tr = document.createElement('tr');
  if (row.rank <= 3) tr.className = 'top' + row.rank;
  if (key && row.player_key === key) tr.className += ' me';
  if (pinned) tr.className += ' pinned';
  tr.appendChild(cell('td', row.rank, 'rank'));
  tr.appendChild(cell('td', row.player, 'player'));
  tr.appendChild(cell('td', row.score, 'score'));
  if (view.id === 'volume') tr.appendChild(cell('td', row.runs, 'num'));
  else tr.appendChild(cell('td', row.level || '', 'num'));
  tr.appendChild(cell('td', fmtWhen(row.created_at, view.id), 'when'));
  return tr;
}

function renderStats(summary) {
  el.stats.innerHTML = '';
  const items = [
    ['Best ever', summary.best],
    ['This season', summary.best_season],
    ['Today', summary.best_today],
    ['Deepest level', summary.deepest_level],
    ['Patches deployed', summary.patches],
    ['Runs', summary.runs],
  ];
  for (const [label, value] of items) {
    const box = document.createElement('div');
    box.className = 'stat';
    box.appendChild(cell('span', label, 'k'));
    box.appendChild(cell('span', value, 'v'));
    el.stats.appendChild(box);
  }
  el.recent.innerHTML = '';
  for (const run of summary.recent || []) {
    const tr = document.createElement('tr');
    tr.appendChild(cell('td', run.score, 'score'));
    tr.appendChild(cell('td', run.level, 'num'));
    tr.appendChild(cell('td', fmtDuration(run.duration_ms), 'num'));
    tr.appendChild(cell('td', new Date(run.created_at).toLocaleString([], {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    }), 'when'));
    el.recent.appendChild(tr);
  }
  el.you.hidden = false;
}

function renderFame(entries) {
  el.fameRows.innerHTML = '';
  if (!entries || !entries.length) {
    el.fame.hidden = true;
    return;
  }
  for (const entry of entries) {
    const tr = document.createElement('tr');
    tr.appendChild(cell('td', entry.label));
    tr.appendChild(cell('td', entry.player, 'player'));
    tr.appendChild(cell('td', entry.score, 'score'));
    tr.appendChild(cell('td', entry.players, 'num'));
    el.fameRows.appendChild(tr);
  }
  el.fame.hidden = false;
}

async function load() {
  const view = VIEWS.find((v) => v.id === current) || VIEWS[0];
  buildTabs();
  el.blurb.textContent = view.blurb;
  renderHead(view);
  el.rows.innerHTML = '';

  const key = nameKey(player);
  const query = new URLSearchParams({ view: current, limit: '10' });
  if (player) query.set('player', player);

  let data;
  try {
    const res = await fetch(API + 'board?' + query.toString());
    data = await res.json();
  } catch (err) {
    el.empty.textContent = 'Could not load the board. Try again in a moment.';
    el.empty.hidden = false;
    return;
  }

  for (const row of data.rows || []) el.rows.appendChild(renderRow(row, view, key, false));
  if (data.pinned) el.rows.appendChild(renderRow(data.pinned, view, key, true));
  el.empty.hidden = (data.rows || []).length > 0;
  if (!el.empty.hidden) el.empty.textContent = 'No runs posted yet. Be the first.';

  renderFame(data.hall_of_fame);

  if (player) {
    try {
      const res = await fetch(API + 'player/' + encodeURIComponent(player));
      if (res.ok) renderStats(await res.json());
      else el.you.hidden = true;
    } catch (err) {
      el.you.hidden = true;
    }
  }
}

const params = new URLSearchParams(location.search);
if (VIEWS.some((v) => v.id === params.get('view'))) current = params.get('view');
player = params.get('player') || readStore(CONFIG.playerKey);
load();
