# Zip & Patches Leaderboard

A single-container FastAPI app that runs the leaderboard **and** a built-in Teams
message collector + next-day score finalizer. Everything runs in one container —
no separate service, no external cron.

## Why the collector exists

A Power Automate flow can only read ~50 messages per call from the Teams group
chat (hard API ceiling, no reliable pagination, and standalone Microsoft Graph
access was refused by the tenant admin). So instead of pulling and scoring all at
once:

1. Power Automate POSTs **raw Teams message pages** to `/collect` frequently.
2. We **buffer + dedupe** them in a small SQLite store (keyed by Teams message id).
3. A daily in-process job scores **yesterday's** buffered messages through the
   existing leaderboard logic.

This also gives the desired "hold scores back until the next day" behavior
automatically.

## Endpoints

Existing leaderboard endpoints are unchanged: `/ingest`, `/leaderboard`,
`/history`, `/reset`, `/adjust`, `/`, `/player`. The accommodation and screenshot
backfill pages are described further down, and the three side games, Flappy Duck,
PatchMan and Patchaga, have their own sections. `GET /games` is the index that
links to all three, reached from the dashboard nav bar. Until it existed the games
were reachable only by typing the URL, since nothing on the site linked to them.

### `POST /collect`

Accepts a Teams "Get messages in a chat" page. It is liberal in what it accepts —
a bare JSON list, `{"value": [...]}`, or `{"body": {"value": [...]}}`. For each
message it reads `id`, `createdDateTime`, `from.user.displayName`, `body.content`,
and `messageType`, computes the local date in `ZS_TIMEZONE`, and upserts
(deduping on the Teams `id`).

* Returns `{ "received": N, "new": M }`.
* If `ZS_COLLECTOR_TOKEN` is set, the request must include a matching `X-Token`
  header, otherwise it returns `401`.

### `POST /finalize?date=YYYY-MM-DD`

Scores the buffered messages for a given local date (defaults to **yesterday** in
`ZS_TIMEZONE`) and feeds them through the existing `process()` leaderboard logic.
This is the manual / backfill / test hook.

* Returns `{ "date": ..., "count": ..., "result": ... }`.
* `process()` has day-level idempotency, so re-running the same date returns
  `{"status": "already_processed"}` — re-runs are safe.

A line is kept only if its cleaned body looks score-ish (contains `//` **or**
starts with a digit). Messages whose `messageType` is set and not `"message"`
(e.g. `systemEventMessage`) are ignored.

## Accommodation requests (PTO without leaving Teams installed)

Anyone heading out shouldn't have to keep Teams on their phone just to dodge the
worst-score+1 penalty. They file an accommodation request on the site instead; once
the Games Commissioner approves it, every covered day is recorded as **excused**
rather than penalized.

An excused day is stored in `history.json` as
`{"zip": 0, "patch": 0, "total": 0, "penalty": false, "excused": true}` and is
deliberately invisible to scoring: it earns no penalty, adds nothing to the player's
totals, doesn't count as a played day or a missed day, doesn't break a streak, and is
excluded from every daily rank, winner, and weekly total. (Without that exclusion an
away player's total of 0 would silently win the day.)

| Page | Who | Purpose |
| ---- | --- | ------- |
| `GET /accommodation` | player | The request form: name, date range, type, optional reason, promise, signature |
| `GET /accommodations` | anyone | Leave board showing every request and its status |
| `GET /backfill` | player | Submit a score screenshot for a past day |
| `GET /commissioner` | commissioner | Approve/deny requests, review and apply screenshots |
| `GET /api/accommodations?status=` | anyone | JSON list of requests |

Approval is **retroactive**. The nightly finalizer usually stamps a penalty before
anyone gets around to approving, so approving reaches back over the requested range,
converts each recorded penalty day to excused, and reverses the matching
`penalty_days`, `days`, score totals, and daily-win counters. Reversing a decision
works the same way in the other direction: denying a request that was already
approved puts those days back to the day's worst real score +1.

Requests are rejected when the name is not on the leaderboard, with a suggestion of
the closest match. Case and extra spaces are forgiven and mapped to the board's
spelling, but a genuine typo is refused rather than filed. An accommodation approved
under a name nobody has would excuse nobody, and a backfill under one would invent a
new player; both fail silently, so they are stopped at the form instead.

`/commissioner` is not linked from the leaderboard, so pending work is surfaced two
other ways instead: a count in the dashboard footer and a banner on the leave board.
Neither links to the page.

### Screenshot backfill

The promise on the form is that the player keeps playing and sends screenshots. `/backfill`
is where those land: player, day played, zip, patches, and a required image. Every field
arrives as text and is parsed in the handler, so a bad entry returns to the form with an
explanation instead of FastAPI's raw 422 page, and the entries survive the round trip.
Uploads are validated by magic bytes rather than the filename (PNG, JPEG, GIF, WebP, AVIF,
BMP), capped at `ZS_MAX_PROOF_BYTES`, stored under `ZS_PROOF_DIR` with a generated UUID
filename, and restricted to past days so a backfill can never pre-empt today's Teams
collection. HEIC and TIFF are refused by name with instructions, because browsers cannot
display them and a screenshot the commissioner cannot see is worse than a clear rejection.

Once a proof has been decided for longer than `ZS_RETENTION_DAYS`, its image file is
deleted and the row is kept as the audit trail. Without that, 8 MB uploads accumulate on
the volume forever.

The commissioner reviews each screenshot next to the claimed numbers, can correct them,
then applies it. Applying replaces the excused (or penalty, or existing) entry with the
real score and reconciles `leaderboard.json` — totals, played days, penalty days, and the
affected day's win counters. If the day was never scored at all, it is finalized from the
message buffer first so the other players still get their normal treatment. The player
page marks these days `BACKFILLED`.

### Commissioner access

`/commissioner` asks for `ZS_COMMISSIONER_TOKEN` once and remembers it in an HttpOnly
cookie. Screenshot images are only served to an authenticated commissioner. If
`ZS_COMMISSIONER_TOKEN` is unset the page stays open (so a missing variable can't lock
everyone out) and shows a warning banner.

## Flappy Duck (side game)

`/flappy` is a small arcade game with a rubber duck, a Patch My PC theme, and its own
leaderboard. It shares the app and the data volume and nothing else.

### Isolation guarantee

Flappy Duck never opens `leaderboard.json` or `history.json` at all, for reading or for
writing. It never touches a daily rank, penalty, streak, excused day, weekly total, or win
counter, and it adds no job to the scheduler. Names are typed by the player rather than
looked up, so the module has no reason to read the real roster and does not reference it.

All server code lives in `app/flappy.py`. `app/main.py` gains exactly two lines: the import
and the `include_router` call. Nothing else in it changes, which keeps the live scoring path
out of the blast radius.

### Player names

A name is free text. Anyone can type whatever they want and post a run under it, so the
game is open to people who are not on the real leaderboard at all.

Names are sanitized rather than validated: control characters are stripped, runs of
whitespace collapse to one space, the result is trimmed, and anything past 60 characters is
cut. An empty name is the only one that is refused.

Runs are grouped by the lowercased name, so `andrew siebert` and `Andrew Siebert` are one
player on the board. The spelling shown is the one from that player's most recent run, which
means changing your capitalization renames you rather than splitting you in two.

### Routes

| Route | Purpose |
| ----- | ------- |
| `GET /flappy` | The game |
| `GET /flappy/board` | The leaderboard, four views |
| `GET /flappy/static/*` | Game files, served from `app/flappy/` |
| `POST /flappy/api/session` | Start a run and get the world to play it in |
| `POST /flappy/api/beat` | Progress ping while a run is in play |
| `POST /flappy/api/score` | Submit a finished run |
| `GET /flappy/api/board` | Board data; `view` is `alltime`, `season`, `today`, or `volume` |
| `GET /flappy/api/player/{name}` | One player's bests, totals, ranks, and recent runs |
| `GET /flappy/api/health` | Which database the container opened, and what is in it |

### Tables

Two tables in the existing SQLite file on `./data:/home`. `flappy_runs` holds every run,
and `flappy_sessions` holds the worlds handed out and when. Every table this module creates
is prefixed `flappy_`, so it is obvious at a glance what belongs to the game. Every run is
stored, not just personal bests, which is what makes the "most deployed" view possible.
Input traces are pruned after 30 days; the score row itself is kept forever.

There is no season column, and there never will be. The four views are the same rows with a
different date window applied, worked out in local time when the board is queried. Nothing
rolls a season over, so nothing can be late or run twice.

### Keeping the board honest

The game is deterministic, and that cuts both ways. It is what makes a run checkable, and it
is also what made the first version trivially forgeable: `sim.mjs` and `config.mjs` are
static files, so anyone can import them, search offline for a perfect set of inputs in a few
milliseconds, and post the result. It replays like a real run because it is one, just not one
anybody played. That is not hypothetical; it is how the board was first broken.

So replaying a submission is the floor rather than the ceiling. Replay proves a trace is
self-consistent. It cannot prove a person produced it. The checks look instead at whether the
run cost what a run costs.

| What is checked | How |
| --------------- | --- |
| The world | The seed is issued by `POST /flappy/api/session`, is never chosen or shown by the client, and is spent the moment a run is posted against it |
| The score | The server replays the trace and records what the replay says. A claimed score the trace does not produce is refused |
| The clock | The session starts when the seed is handed out, and heartbeats mark progress while the run is in play. A run that simulates two minutes has to have taken two minutes |
| The hand | Taps land on a spread of intervals, because hands are not clocks. Machine timing shows up as a spike on one exact value |

The hand check is measured rather than guessed. Across 800 simulated played runs the largest
share of gaps landing on a single exact value was 0.20; across 286 runs from solvers with no
timing noise the smallest was 0.32. `MODAL_SHARE_LIMIT` sits at 0.28, in the empty space
between them, and only applies once a run has enough inputs to have a shape at all.

A run that fails these is stored and answered with HTTP 202 and `counted: false`, along with
the reason. It stays in the table for review rather than disappearing, and the board only
shows runs with `verified = 1`.

What this deliberately does not claim: someone patient enough to pace a forged run in real
time and scatter its timing can still get through. Closing that would need something a
browser game cannot offer. The win is that cheating now costs the same wall clock time as
playing, which is the practical limit here.

Every threshold is a named constant near the top of the "Deciding whether a run happened"
section of `app/flappy.py`, with the reasoning next to it.

Runs recorded before this existed are re-judged once, at import. A trace that does not
reproduce its own score is voided, and one that replays but was computed rather than played
is caught by the same hand checks. A score stored next to an empty trace is voided too,
because pruning empties the column rather than writing an empty list, so a score with nothing
beside it was typed rather than played. Runs whose trace has actually been pruned are kept,
because unjudgeable is not the same as suspect.

One wrinkle worth knowing if you touch this code. The release that shipped the game stored
each trace twice: `clean_trace()` returned `json.dumps(list)` and the insert called
`json.dumps()` on that string again, so every row written before this change holds a JSON
string containing a JSON array. `decode_trace()` reads both shapes. Reading only the current
one parses to a string rather than a list, files the whole existing board under "no trace",
and clears nothing, which is exactly what the first attempt at this did. The fixtures in
`tools/check_audit.py` write the legacy shape on purpose for that reason.

`tools/flappy_admin.py` is there for the cases a person has to decide:

```
python tools/flappy_admin.py suspects          # what was held back, and why
python tools/flappy_admin.py show 41           # one run, with its timing evidence
python tools/flappy_admin.py void 41 --why "posted from a script"
python tools/flappy_admin.py restore 41
python tools/flappy_admin.py recheck           # judge everything again after moving a threshold
python tools/flappy_admin.py info              # which database is in use, and what is in it
```

### Running any of this on the server

Everything in `tools/` is a workstation tool. A deploy here is uploading `app/` through File
Station, so `tools/` never reaches the NAS at all, and the container mounts only `./app` and
`./data` regardless. Point the commands above at a copy of the database with `--db`.

The two things that genuinely have to happen on the server are therefore in `app/flappy.py`,
which is the one folder that does get uploaded. Neither needs a shell:

```
GET /flappy/api/health
```

reports which database file the container actually opened, whether `ZS_BUFFER_DB` overrode the
path, its size and timestamp, any `-wal` sidecar, every table in it, and how many runs are on
the board, held back, or never judged. That last count is the one that matters after a deploy:
it should be zero, because the audit runs at import.

`folder_is_a_mount` is the field to read first when a downloaded copy of the database disagrees
with the board. If it is false, `/home` was never bind mounted, the database is inside the
container's own writable layer, and there is no copy of it on the NAS to download. That
survives restarts, so nothing looks broken, but the folder being browsed is a directory the app
has never written to.

Two other things it settles. `buffer`, `accommodations`, and `proofs` come from `main.py` and
`flappy_runs` and `flappy_sessions` come from `flappy.py`, and both open the same path in the
same process, so a file holding one set without the other cannot be the live one and is a stale
copy. And a `-wal` sidecar means a copy taken without it is missing whatever that file still
holds, which can include whole tables.

It exists because the board and a downloaded copy of the file can honestly disagree, and the
reasons are not guessable from outside. A `ZS_BUFFER_DB` override, a bind mount pointing
somewhere other than the folder being browsed, or a WAL-mode database copied without its `-wal`
sidecar all produce a file that is missing tables rather than one that looks broken.

For anything that changes data:

```
docker exec <container> python -c "import sys; sys.path.insert(0, '/app'); import flappy; print(flappy.clear_board())"
```

If you ever do want the full review tool on the server, upload `tools/flappy_admin.py` into
`app/` and run `python /app/flappy_admin.py suspects`. It resolves `flappy.py` as a sibling of
its own folder, and in the container that resolves to `/app` either way, so it needs no changes.
FastAPI is already installed there, which it would not be on the NAS itself.

To wipe the board and start over, which is the honest option when the scores already there
are not worth sorting through:

```
python tools/flappy_admin.py clear                      # every run, asks first
python tools/flappy_admin.py clear --player "Some Name" # one player
```

On the server, as above, that script is not there. The deletion itself lives in
`flappy.clear_board()` for that reason, so it can be reached from inside the container:

```
docker exec <container> python -c "import sys; sys.path.insert(0, '/app'); import flappy; print(flappy.clear_board())"
```

Add a name to clear one player: `flappy.clear_board('Some Name')`. Either route only ever
deletes from the `flappy_` tables. Keeping the statement in the module rather than in a
command to copy is deliberate, since the alternative is hand typing `DELETE` against the same
database file the real leaderboard's buffer lives in.

### Tuning

Every constant that affects how the game feels lives in `app/flappy/config.mjs`. Gravity,
flap strength, scroll speed, gap size, obstacle spacing, hitbox inset, and rotation are all
there, and nothing else hardcodes them. Difficulty is deliberately constant: the gap never
shrinks and the scroll never speeds up.

The simulation in `app/flappy/sim.mjs` is deterministic. It runs on a fixed 1/120 s timestep
with rendering interpolated separately, so the same seed and the same inputs produce the same
run at any frame rate. `app/flappy.py` carries a port of that simulation so the server can
replay a submission for itself, which is only sound while the two agree exactly, so
`tools/check_sim_parity.py` runs both engines over the same traces and compares every field.

Five checks worth running after a tuning change:

| Command | What it proves |
| ------- | -------------- |
| `node tools/test_sim.mjs` | The simulation is deterministic and the physics behave |
| `node tools/probe_difficulty.mjs` | A competent player can still clear ten obstacles |
| `python tools/check_sim_parity.py` | The Python and JavaScript simulations still agree |
| `python tools/check_audit.py` | Forged runs are voided and played runs are kept |
| `python tools/check_flappy_api.py <base-url> <data-dir>` | The API works and the real leaderboard files are untouched |

Point the last one at a throwaway data directory, never the live one.

### Art

All artwork is original and generated by `tools/make_flappy_atlas.py`, a one-time authoring
script that writes `app/flappy/atlas.png` with the standard library only. The app never
imports it. Sound effects are synthesized in the browser with WebAudio, so there are no audio
files to ship, and the game starts muted.

No build step, no bundler, no npm, no CDN, and no new Python dependencies. Deploy is still
upload files and restart.

## PatchMan (side game)

`/patchman` is a maze chase on a circuit board. You are a patch, the four things hunting you
are vulnerabilities, and the Patch My PC logo is what turns them from a threat into a job.
It shares the app and the data volume with the leaderboard and nothing else.

It is built the same way as Flappy Duck and repeats its rules deliberately, so this section
describes what differs and points at the Flappy Duck section for the rest.

### Isolation guarantee

The same guarantee, and for the same reason. PatchMan never opens `leaderboard.json` or
`history.json`, for reading or for writing. It never touches a daily rank, penalty, streak,
excused day, weekly total, or win counter, and it adds no job to the scheduler. Names are
typed rather than looked up, so it has no reason to read the real roster and does not
reference it.

All server code lives in `app/patchman.py`. `app/main.py` gains exactly two lines, the import
and the `include_router` call, next to the two Flappy Duck already has.

The two games are also isolated from each other. They share no module, no table, and no
constant. PatchMan carries its own copy of the name handling and the board queries rather
than importing Flappy Duck's, so a change to one game's tuning or thresholds cannot move the
other game's board.

### The game

A 27 by 31 board with 247 patches, 4 logos, and a tunnel that wraps left to right.

| Thing | What it is |
| ----- | ---------- |
| PatchMan | You. Eats patches, and outruns everything on the board until RCE decides otherwise |
| Patch | 10 points. Deploying one |
| Patch My PC logo | 50 points, and it makes every vulnerability patchable for a while |
| Release package | Appears twice a level, worth 100 to 5000 depending on how deep you are |
| RCE | Comes straight at you, and speeds up to match you once the board is nearly clear |
| XSS | Aims four tiles ahead of where you are going, so it cuts you off |
| SQLI | Reflects RCE's position through a point ahead of you, so it arrives from the other side |
| 0DAY | Bold at range, shy inside eight tiles |

Vulnerabilities alternate between scattering to their corners and hunting, on a schedule that
gets more hostile each level. Patching one sends its eyes back to the house, where it
reassembles and comes out again.

Scoring is the same shape as the arcade it is a nod to: 200, 400, 800, 1600 for
vulnerabilities patched inside a single window, and the chain resets when the window closes.
Catching all four on one logo is worth 3000, which is more than eating every patch on the
board, so the order you catch them in matters more than how many you catch. Clearing a board
is 500 and starts the next one with the same lives.

### Leaderboard rules

Identical to Flappy Duck, on purpose, so the two boards read the same way: four views
(`alltime`, `season`, `today`, `volume`), one row per player showing their best qualifying
run, earliest first on a tie, ranks computed at query time from a date window rather than
stored, free-text names grouped by lowercase, and every run kept rather than only personal
bests. See the Flappy Duck section for the reasoning; none of it is repeated here because
none of it differs.

The board carries two extra columns PatchMan has and Flappy Duck does not: the level reached
and the number of patches deployed. `volume` ranks on total patches across all runs, which is
the "most deployed" view under a name that fits this game.

### Routes and tables

The same eight routes under `/patchman`, and two tables, `patchman_runs` and
`patchman_sessions`. `patchman_runs` adds `level` and `patches` to the columns Flappy Duck
stores, and its trace column is `turns` rather than `flaps`. Every table this module creates
is prefixed `patchman_`.

### Keeping the board honest

The same four checks (the world, the score, the clock, and the hand) apply for the same
reason, and with the same limits. `app/patchman.py` replays every submitted run from its seed
and its keystrokes before counting it.

One difference is worth knowing. Flappy Duck's thresholds were calibrated against 800
measured played runs and 286 solver runs. PatchMan has no such corpus yet, so its equivalents
are set deliberately loose and are marked provisional in the file:

| Constant | Flappy Duck | PatchMan | Why |
| -------- | ----------- | -------- | --- |
| `MODAL_SHARE_LIMIT` | 0.28 | 0.35 | Turns happen on tile centers, so even a hand's gaps cluster more than a flap's |
| `MODAL_MIN_INTERVALS` | 25 | 30 | A maze run has fewer inputs, so it needs more of them before the shape means anything |
| `MIN_HUMAN_GAP_TICKS` | 4 | 3 | Tapping a direction twice at a corner is normal here and is not evidence of a script |
| `MAX_SHORT_GAPS` | 8 | 15 | Same reason, applied to how many times it may happen in one run |

The input rate cap is the one that is not a loosening: 4.0 turns per second against Flappy
Duck's 4.5 flaps per second, because turning is a rarer act than flapping and a real player
never approaches it.

A false positive costs more than a false negative on an office game, so these are set to let
a careful forgery through rather than to refuse an honest run. Tighten them once there are
real runs to tighten them against, and use `recheck` afterwards.

`tools/patchman_admin.py` mirrors `tools/flappy_admin.py` command for command:

```
python tools/patchman_admin.py suspects          # what was held back, and why
python tools/patchman_admin.py show 41           # one run, with its timing evidence
python tools/patchman_admin.py void 41 --why "posted from a script"
python tools/patchman_admin.py restore 41
python tools/patchman_admin.py recheck           # judge everything again after moving a threshold
python tools/patchman_admin.py clear             # every run, asks first
```

`recheck` re-runs the hand checks only. The clock checks were decided against a session's
heartbeats, and heartbeats are not kept, so a recheck carries those verdicts forward instead
of recomputing them. Without that it would silently clear every run held back for not being
played in real time, which is the one thing a recheck must not do. The flags it refuses to
touch are named in `CLOCK_FLAGS`.

As with Flappy Duck, the deletion itself lives in `patchman.clear_board()` so it can be
reached inside the container, where `tools/` is not mounted:

```
docker exec <container> python -c "import sys; sys.path.insert(0, '/app'); import patchman; print(patchman.clear_board())"
```

### Tuning

Every constant lives in `app/patchman/config.mjs`: speeds per level tier, the scatter and
chase schedule, how long a logo's window lasts, how many patches release each vulnerability
from the house, bonus values, and the maze itself. `app/patchman.py` mirrors them, and the
two must not drift. `CONFIG.maxTicks` and `ABSOLUTE_MAX_TICKS` in particular have to stay
equal, because the JavaScript uses one as its budget and the Python uses the other.

The simulation runs on a fixed 1/120 s timestep in integer sub-units, with rendering
interpolated separately, so the same seed and inputs produce the same run at any frame rate.
Positions are exact integers and a tile center is an equality test rather than a tolerance,
which is what lets the Python port match the JavaScript exactly instead of approximately.

Three checks worth running after a change:

| Command | What it proves |
| ------- | -------------- |
| `python tools/make_patchman_maze.py --check` | The checked-in maze is still symmetric, sealed, fully reachable, and free of dead ends and 2x2 rooms |
| `python tools/check_patchman_parity.py` | The Python and JavaScript simulations still agree, field for field |
| `python tools/patchman_smoke.py` | The API works end to end: a played run is replayed, believed, ranked, and a forged one is not |

Both of the last two drive the game with `tools/patchman_bot.mjs`, a scripted player that
chases patches and runs from anything hunting it. It is a trace generator rather than a test,
since it prints JSON and judges nothing, but it is also the difficulty probe, because a
change that makes the game unplayable shows up as the bot's scores and levels collapsing in
the smoke test's output.

The parity check is the important one. The server's verdict is only sound while the two
engines are the same game, so it drives both over the same traces, including deliberately
degenerate ones like an empty trace, three inputs on the same tick, and a run that pauses for
75 seconds. It compares 27 fields with no tolerance, including the entire remaining maze as a
string, which is what catches a patch eaten one tick apart.

`tools/patchman_smoke.py` and `tools/patchman_shotcatch.py` are development tools. The smoke
test owns the server's clock so a three-minute run can be checked without waiting three
minutes; the shot catcher gives the browser somewhere to post `canvas.toDataURL()` so the
rendering can be looked at as an image rather than guessed at. Both write only to throwaway
paths.

### Art

All artwork is original. The maze is generated and validated by
`tools/make_patchman_maze.py`, and everything on screen (walls, vulnerabilities, patches, the
release package) is drawn as vectors by `app/patchman/render.mjs` at run time. There are no
sprite sheets and no third-party assets. This is a maze chase, which is a genre; nothing here
is copied from any particular one.

The one real image is the Patch My PC logo, which `tools/make_patchman_assets.py` turns into
the three files the page needs. It arrives flattened onto solid white, so the script undoes
the white rather than keying it out (`a = 1 - min(r,g,b)/255`, then un-composite), which
leaves edges fading to transparent instead of to a white halo. The app never imports that
script.

A patchable vulnerability is Patch My PC green. Blue was the first choice and the wrong one,
because these walls are already blue and a blue vulnerability disappeared into them at
exactly the moment the player most needs to see where it is.

Color is not the only cue for that state. A hunting vulnerability has open eyes on its head;
a patchable one wears crossed-out eyes, so the change is legible without relying on the green.
On the flash frames that warn the window is closing, the body goes near-white and the eye
crosses invert to dark rather than staying pale, which would otherwise erase them.

The four are beetles rather than the usual silhouette, because a vulnerability is a bug and
the joke is worth the drawing. Each is a shell with a head that sits proud of the leading
edge, twitching antennae, and six jointed legs walking a tripod gait: front and back on one
side swing with the middle leg on the other, which is how a real one moves. The head and the
raked legs both point the way it is going, so direction is readable at a glance without
reading the eyes.

The legs are a pale wash of the shell color rather than the black a real beetle has, for a
practical reason: black legs on a near-black maze made every one of them read as a floating
disc. They also reach past the shell on purpose, because the spiky silhouette is what
separates a bug from a ball at the size these are actually drawn.

Each shell carries its own spot layout, so the four stay apart for anyone who cannot rely on
the color: RCE has two shoulder spots and a tail spot, XSS has four in a square, SQLI has
three in a triangle, and 0DAY has a matched pair.

Sound is synthesized in the browser with WebAudio, so there are no audio files, and the game
starts muted.

## Patchaga (side game)

`/patchaga` is a fixed shooter in the Galaga mould. A rubber duck at the bottom of the
screen fires Patch My PC logos at a formation of bugs that peel off and dive at it. Clear a
wave and the next one is worse. It shares the app and the data volume with everything else
and nothing more, on the same terms as Flappy Duck: no `leaderboard.json`, no
`history.json`, no scheduler job, no roster lookup, two lines in `app/main.py`, and every
table it creates prefixed `patchaga_`.

Names work exactly as they do in Flappy Duck, and for the same reasons.

### Routes

| Route | Purpose |
| ----- | ------- |
| `GET /patchaga` | The game |
| `GET /patchaga/board` | The leaderboard, four views |
| `GET /patchaga/static/*` | Game files, served from `app/patchaga/` |
| `POST /patchaga/api/session` | Start a run and get the world to play it in |
| `POST /patchaga/api/beat` | Progress ping while a run is in play |
| `POST /patchaga/api/score` | Submit a finished run |
| `GET /patchaga/api/board` | Board data; `view` is `alltime`, `season`, `today`, or `volume` |
| `GET /patchaga/api/player/{name}` | One player's bests, totals, ranks, and recent runs |

### The thing that makes this game hard to build

The server replays every submission in Python. That only proves anything while the Python
and the JavaScript agree to the last bit, and a shooter makes agreeing much harder than a
side-scroller did, because bugs fly curved paths and curves want trigonometry.

Two hazards, and both are solved by refusing to have the problem:

- **Floating point drift.** Nothing in the simulation stores a float. Positions are
  integers in sub-units, 64 of them to the pixel, and every division floors —
  `Math.floor(a / b)` in JavaScript, `a // b` in Python. Rounding is written as
  `floor(x + 0.5)` in both, because JavaScript's `Math.round` rounds halves up and Python's
  `round` rounds them to even, so the two disagree on exactly the values a game hits most.
- **Transcendental functions.** `Math.sin` and `math.sin` are not specified to agree, and in
  practice they don't. So the simulation never calls one. Angles are integer steps around a
  circle of 1024, and sine comes from a table built at import with integer-only arithmetic
  (Bhaskara I's approximation, evaluated so every operand stays a positive integer small
  enough to be exact in a double). Both languages build the same table from the same
  expression and get the same 1024 numbers.

`tools/check_patchaga_parity.py` is what keeps this honest. It runs both engines over the
same traces and compares a digest of the entire world on **every tick**, so a disagreement
is reported as the first tick it happened on rather than as a wrong final score. Half the
cases are random input and half are played by the bot, deliberately: random input dies in
wave 1 and never reaches a capture, a merged duck or a regression sweep, and the bot reaches
all three but never wanders into an ugly corner. Run it after touching the simulation, and
believe nothing until it passes:

```
python tools/check_patchaga_parity.py --cases 24 --ticks 60000 --verbose
```

### Keeping the board honest

The same argument as Flappy Duck: replay proves a trace is self-consistent, not that a
person produced it. The world is server-issued, the score is whatever the replay says, and
the clock is anchored by heartbeats.

The hand check is where this game had to differ, and the difference is worth understanding
before touching it. The duck fires one patch per press and ignores a held key, so fire looks
hand-timed — but it isn't. A press is recorded at the first tick the duck was *allowed* to
shoot, not when the key went down, because the client only queues a press that will produce
a patch. The cooldown and the cap on patches in the air therefore quantise the fire stream
onto a grid the player has no say in, and measuring it for regularity would find the
cooldown on every run, for everyone. A steer carries no such gate: it is recorded when the
key moved, so its spacing really is the hand's. So:

- **Steering is judged on its timing.** Rate, interval spread, and double presses all read
  the steering stream only.
- **Firing is judged on its result.** A solver picks the tick that hits; a hand does not.
  The check is the hit rate over a run with enough shots to have one.

The accuracy threshold sits at 92%, which is high on purpose. There is no corpus of human
runs to calibrate against, and the reference bot — which aims perfectly but only fires when
a shot is available — lands between 38% and 75%, median 56%. A true solver would sit near
100%. Guessing low would reject honest players to catch nobody, so the threshold is placed
where only a search could reach.

The timing thresholds are measured rather than guessed, using the bot's two modes as a
bracket. Its default mode re-decides direction every tick, and across six runs produced 3.8
to 12.4 steers per second with 42% to 66% of gaps landing on one value. Its `--human` mode
adds a reaction delay and a dead zone, and produced 1.0 to 1.4 steers per second with 4% to
9%. The limits sit in the gap between them: 6 steers per second, and a 0.35 modal share.

One subtlety, because it is the kind of thing that is easy to get wrong and hard to notice.
Moving from left to right is one motion of one hand, but a browser reports it as two events
microseconds apart — the left key coming up, then the right key going down. The client
records both faithfully, so the trace holds a neutral and a direction on the same tick.
Counted as two inputs that reads as a hand pressing twice in under a hundredth of a second,
which is what `double_inputs` exists to catch, and it would have caught every player who
ever changed direction quickly. So `steering_codes()` drops the release half of a roll before
any statistic is computed. It removes the neutral and never the direction that follows it, so
a solver cannot use it to launder its rate: its presses stay on the ticks it made them on.
This was found by playing the game in a browser and watching an honest run get refused.

Runs that fail are stored with `counted: false` and the reason, and reviewed by hand:

```
python tools/patchaga_admin.py suspects          # what was held back, and why
python tools/patchaga_admin.py show 41           # one run, with its timing and aim evidence
python tools/patchaga_admin.py void 41 --why "posted from a script"
python tools/patchaga_admin.py restore 41
python tools/patchaga_admin.py recheck           # judge everything again after moving a threshold
python tools/patchaga_admin.py clear             # wipe the board, asks first
```

`patchaga.clear_board()` exists for the same reason `flappy.clear_board()` does — the admin
script is not in the container.

### Tuning

Every constant that affects how the game feels lives in `app/patchaga/config.mjs`, mirrored
into `app/patchaga.py`. Change one and the two engines disagree, so the parity gate is not
optional after a tuning pass.

| Command | What it proves |
| ------- | -------------- |
| `node tools/patchaga_bot.mjs 30 --verbose` | The game is playable and how far a competent player gets |
| `node tools/patchaga_bot.mjs 8 --human` | What an honest hand looks like, for the timing checks |
| `python tools/check_patchaga_parity.py` | The Python and JavaScript simulations still agree, tick for tick |
| `python tools/patchaga_smoke.py` | The whole API works end to end, honest runs count, and forged ones don't |

The smoke test builds its own throwaway database in the temp directory and asserts at the
end that only `patchaga_` tables were created, so it can be run anywhere without a target.

### Art

Original, generated by `tools/make_patchaga_assets.py` with Pillow, and committed — the app
never imports the generator. Everything in the game itself is drawn with canvas primitives at
runtime, so there are no sprites to load. Sound is synthesized with WebAudio, so there are no
audio files either, and the game starts muted.

No build step, no bundler, no npm, no CDN, and no new Python dependencies.

## Environment variables

| Variable              | Default                      | Purpose                                            |
| --------------------- | ---------------------------- | -------------------------------------------------- |
| `ZS_TIMEZONE`         | `America/Chicago`            | Local TZ for "yesterday" and the daily scheduler   |
| `ZS_BUFFER_DB`        | `/home/zipscores_buffer.db`  | SQLite buffer path (under `./data:/home`, persists) |
| `ZS_COLLECTOR_TOKEN`  | `` (empty)                   | If set, `/collect` requires `X-Token` to match     |
| `ZS_RETENTION_DAYS`   | `30`                         | Prune buffered messages and decided proof images older than this |
| `ZS_FINALIZE_HOUR`    | `2`                          | Daily finalize run hour (local time)               |
| `ZS_FINALIZE_MINUTE`  | `10`                         | Daily finalize run minute (local time)             |
| `ZS_COMMISSIONER_TOKEN` | `` (empty)                 | Passcode for `/commissioner`; empty leaves it open |
| `ZS_PROOF_DIR`        | `/home/proofs`               | Where backfill screenshots are stored (persists)   |
| `ZS_MAX_PROOF_BYTES`  | `8388608`                    | Max screenshot upload size (8 MB)                  |
| `ZS_STATE_FILE`       | `/home/leaderboard.json`     | Cumulative per-player state                        |
| `ZS_HISTORY_FILE`     | `/home/history.json`         | Per-day scores                                     |

The accommodation, proof, and Flappy Duck tables live in the same SQLite file as the message
buffer, so they persist on the existing `./data:/home` volume with no extra setup. Flappy
Duck adds no environment variables of its own; it reads `ZS_BUFFER_DB` and `ZS_TIMEZONE`,
and nothing else.

`STATE_FILE` (`/home/leaderboard.json`) and `HISTORY_FILE` (`/home/history.json`)
are unchanged. The buffer DB lives on the same `./data:/home` volume.

## Power Automate flow shape

```
Recurrence (every 5–15 min)
  -> Get messages in a chat   (Top 50, pagination OFF)
  -> HTTP POST                (raw action body) to
                              https://siebe41.synology.me/collect
```

If `ZS_COLLECTOR_TOKEN` is set, add an `X-Token` header to the HTTP action.

## Deploy

The app runs as a **single container with no image build required**. `docker-compose.yml`
uses a stock `python:3.11-slim` image and bind-mounts the code + brand assets from
`./app` into the container; dependencies are installed on container start. Nothing is
baked into a custom image, so **you never need to rebuild** — you only swap files and
restart.

Folder layout on the NAS (project root, e.g. `/docker/leaderboard-api`):

```
docker-compose.yml
dockerfile              # only used for the optional "build elsewhere + import" path
deploy.ps1
app/                    # mounted to /app in the container
  main.py
  requirements.txt
  zippatchlings.ico     # served at /favicon.ico
  zippatchlings.png     # served at /logo.png
data/                   # mounted to /home  (leaderboard.json, history.json, buffer DB)
  proofs/               # backfill screenshots (created on first upload)
```

### Deploy without SSH (Synology Docker / Container Manager GUI)

1. In **File Station**, upload the changed files into the `app/` folder (overwrite),
   e.g. `app/main.py` and the brand assets.
2. In the **Docker** / **Container Manager** app, select the `leaderboard-app`
   container and click **Restart** (Action → Restart).

Restarting re-runs the container command (`python /app/start.py`, which does
`pip install -r requirements.txt` then launches `uvicorn main:app ...`), which picks
up the new mounted `main.py`. First boot after a `requirements.txt` change is
~20–30 s slower while deps install.

> The favicon (`/favicon.ico`) and logo (`/logo.png`) are served straight from the
> mounted `app/` folder — updating an image is just a File Station upload + restart.

### First-time switch to the no-build setup (one-time, no SSH)

The very first time you move to this layout the container has to be **recreated**
(its image and volume mounts changed), so a plain Restart isn't enough. Do this once:

1. Upload the new `docker-compose.yml` and the `app/` + `data/` folders into the
   project directory via **File Station**.
2. **Container Manager (DSM 7.2+):** create/open a **Project** pointing at
   `docker-compose.yml` and click **Up** (no Build needed). Done.
3. **Legacy Docker app (no compose):** pull `python:3.11-slim` under **Registry**,
   delete the old `leaderboard-app` container, then **Create** a new container from
   that image with:
   - **Port**: `8000` → `8000`
   - **Volumes**: `…/leaderboard-api/app` → `/app`, `…/leaderboard-api/data` → `/home`
   - **Execution command / Entrypoint** — use the Python startup script. A Python
     entrypoint avoids two legacy-Docker pitfalls at once: the Command field splits
     on spaces / ignores quotes (so inline `sh -c "..."` breaks), and shell scripts
     break if they pick up Windows CRLF line endings. `python /app/start.py` is two
     tokens with no quoting, and Python doesn't care about CRLF:
     `python /app/start.py`

After this one-time step, every future deploy is just **upload files → Restart**.

> **Port mapping is a separate setting from volumes/command, and a freshly
> created container starts with none.** When recreating the container by hand in
> the legacy Docker app, it's easy to set the volumes + command but forget the
> port. Double-check **Port Settings → Local Port `8000` → Container Port `8000`
> (TCP)**.

### Troubleshooting: log says "Uvicorn running" but the site times out

This means the **app is healthy but unreachable** — the request never gets into
the container. It is a networking/port problem, not an app problem. Check, in order:

1. **Port mapping** on the container: Local `8000` → Container `8000` (TCP). This is
   the most common cause right after recreating the container — the inside-container
   `0.0.0.0:8000` in the log is *not* enough on its own; a host port must be mapped to it.
2. **Reverse proxy** (DSM → Login Portal → Advanced → Reverse Proxy): the
   `siebe41.synology.me` rule must point at `localhost:8000`. Recreating the
   container doesn't change this, but verify it still targets the mapped host port.
3. **Firewall** / router port forward if reaching it from outside the LAN.

Quick isolation test: browse to `http://<NAS-LAN-IP>:8000/` from a device on the
same network. If that works but the public URL doesn't, the issue is the reverse
proxy / firewall, not the container.

### Troubleshooting: site times out remotely but works over IPv4

If `https://siebe41.synology.me/` hangs in the browser but a direct IPv4 request
succeeds, the culprit is a **stale IPv6 (AAAA) DNS record** on the synology.me
DDNS. The hostname publishes both an A record (IPv4, working) and an AAAA record
(IPv6). If the NAS's IPv6 prefix changed or inbound IPv6 isn't forwarded, the AAAA
is dead — and browsers prefer IPv6, so they try the dead address first and hang.

Diagnose from any machine (no LAN access needed):

```
curl -4 -o NUL -m 20 -w "ipv4 http=%{http_code} ip=%{remote_ip}\n" https://siebe41.synology.me/
curl -6 -o NUL -m 20 -w "ipv6 http=%{http_code}\n"                 https://siebe41.synology.me/
nslookup -type=AAAA siebe41.synology.me
```

If IPv4 returns `200` and IPv6 times out, it's this. Fixes (any one):

1. **Stop publishing IPv6:** Control Panel → **Network → Network Interface → LAN →
   IPv6 → Off**. After the DNS TTL the AAAA disappears and every client uses IPv4.
2. Or disable the IPv6 external address on the **DDNS** entry (External Access →
   DDNS) if your DSM exposes it.
3. Or actually fix inbound IPv6 (router + NAS firewall allow `:443` over v6, and the
   AAAA must match the NAS's current global v6 address).

Immediate client-side workaround: pin IPv4 in `hosts`
(`162.204.54.213  siebe41.synology.me`) and `ipconfig /flushdns`.

### Deploy with SSH (optional)

`deploy.ps1` still works: it tars the project (excluding `data`, `.git`, caches),
SCPs it to the NAS, then runs `docker-compose up -d` on the Synology. With the
no-build compose, `--build` is unnecessary (the script's command is harmless either
way since there is no `build:` directive).
