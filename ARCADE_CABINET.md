# Arcade cabinet requirements

Reference notes for designing a physical cabinet around the three side games
(Flappy Duck, PatchMan, Patchaga). See the main `README.md` for the server
and leaderboard-honesty details this document summarizes.

## Architecture at a glance

One FastAPI container serves everything: the Teams-scores leaderboard *and*
three self-contained arcade games (Flappy Duck, PatchMan, Patchaga) plus a
games index at `/games`. Each game is a FastAPI router (`app/flappy.py`,
`app/patchman.py`, `app/patchaga.py`) mounted with two lines in
`app/main.py`, serving its own static folder (`app/flappy/`,
`app/patchman/`, `app/patchaga/`) and its own SQLite tables. They share the
process and the data volume, nothing else — no shared leaderboard state, no
shared code between games.

For a cabinet, the practical takeaway: **it's a kiosk browser pointed at a
URL**, not a native executable. Whatever machine drives the cabinet just
needs a browser and network access to wherever the container is deployed.

## Engine

Vanilla JS, ES modules, Canvas 2D. No game engine, no framework, no
bundler, no npm, no CDN. Each game is split the same way:

- `sim.mjs` — the deterministic simulation (physics/logic), fixed timestep
- `render.mjs` — Canvas 2D drawing, driven by interpolated sim state
- `game.mjs` — glue: input, the rAF loop, score submission
- `config.mjs` — every tuning constant in one place
- `audio.mjs` — WebAudio, synthesized (no audio files at all)

All three simulations run on a **fixed 1/120s timestep**, with rendering
interpolated between ticks (`alpha` blend) so it looks smooth at any
monitor refresh rate. This determinism exists specifically so the server
can replay a submitted run bit-for-bit — a Python port of each simulation
lives in `app/*.py`.

Art: Flappy Duck has a real sprite atlas (`atlas.png`, built by a one-time
authoring script). PatchMan and Patchaga draw everything with canvas vector
primitives at runtime — no sprite sheets. Either way, nothing is loaded
from a CDN, so it works fully offline once the page is cached.

## Resolution / display

All three games are **fixed-resolution, portrait-oriented** canvases,
scaled to fit:

| Game | Canvas | Aspect |
|---|---|---|
| Flappy Duck | 288×512 | 9:16 |
| PatchMan | 432×560 | ~0.77:1 |
| Patchaga | 432×560 | ~0.77:1 |

Scaling logic (`render.mjs`, same pattern in all three):
```js
const dpr = window.devicePixelRatio || 1;
const fit = Math.min(availW / CONFIG.width, availH / CONFIG.height);
scale = Math.round(fit * dpr);
canvas.width = CONFIG.width * scale;   // backing store
canvas.style.width = CONFIG.width * fit + 'px'; // CSS size
ctx.imageSmoothingEnabled = false;
```
It scales to fit the container while preserving aspect ratio (never
stretches/distorts), snaps to integer pixel multiples scaled by
devicePixelRatio for crisp pixels, and re-runs on `resize`. **This matters
directly for a cabinet monitor**: since all three games are portrait, a
portrait-mounted monitor (or a bezel/window cropped portrait) gives a
native-feeling scale; a landscape monitor will letterbox hard with big side
bars.

## Input handling

No gamepad API support anywhere — everything is keyboard + pointer/touch:

| Game | Keys | Pointer/touch |
|---|---|---|
| Flappy Duck | `Space` / `ArrowUp` = flap, `M` = mute | tap/click anywhere = flap |
| PatchMan | Arrows or `WASD` = direction, `M` = mute | tap toward a direction relative to the player |
| Patchaga | `ArrowLeft`/`A`, `ArrowRight`/`D` = steer, `Space`/`ArrowUp`/`W`/`Enter` = fire, `M` = mute | left/right half of canvas = steer, any touch = fire+hold |

All key handling uses `ev.code` (physical layout, not `ev.key`), ignores
repeats itself (so OS key-repeat can't be abused as autofire/turbo), and
releases held input on window `blur` (PatchMan/Patchaga) so a stuck key
doesn't run the character into a wall forever.

**For a cabinet build**, this is good news: a standard USB/IPAC/ZeroDelay-
style keyboard-encoder joystick+buttons setup (mapping the stick to arrow
keys and buttons to Space/Enter/W-A-S-D) works with zero code changes.
Minimum viable controls: a 4-way or 8-way stick → arrows, one button →
Space/Enter (fire/flap), maybe a second button mapped to `M` for mute. No
analog/gamepad input is used anywhere, so a cheap microswitch stick is
entirely sufficient — nothing is lost by not having analog.

One real gotcha: WebAudio requires a user gesture to unlock
(`audio.unlock()` is called from the first keydown/pointerdown), and games
start muted by default. That's naturally satisfied by a player pressing the
joystick/button, so it shouldn't need special handling, but it's worth
testing on whatever kiosk browser is chosen since some kiosk configs
suppress the "gesture" flag on synthetic/emulated input.

## How the leaderboard talks to the server

Each game follows the same request lifecycle against its own
`/‹game›/api/*` routes:

1. **`POST /‹game›/api/session`** — client asks for a world to play. The
   server generates and returns the **random seed** — the client never
   picks or sees it in advance. This is deliberate: since the physics are
   static, client-side files, a client-chosen seed could be searched
   offline for a "perfect" input sequence and replayed as if played.
2. **`POST /‹game›/api/beat`** — a heartbeat sent periodically (every 5s)
   while a run is in progress, carrying the current simulation tick. This
   anchors a wall-clock check: a 2-minute run has to actually take 2
   minutes of real heartbeats, not be computed in milliseconds and
   submitted.
3. **`POST /‹game›/api/score`** — on death, the client submits
   `{session, player, score, duration_ms, <full input trace>}`
   (flaps/turns/steers — whatever inputs drove that game). The server
   **replays the entire input trace through its own Python port of the
   simulation** and only believes the score the replay itself produces — a
   claimed score that the trace doesn't reproduce is rejected outright.
4. Beyond replay, the server statistically fingerprints the *timing* of
   the input trace (interval spread, rate caps, modal-share-of-identical-
   gaps) to distinguish a human hand from bot/script-perfect timing,
   calibrated per game against measured human vs. solver runs. Runs that
   fail are stored but marked `counted: false` and excluded from the
   board.
5. **`GET /‹game›/api/board`** — reads back four views (`alltime`,
   `season`, `today`, `volume`), computed at query time from date windows
   rather than stored/rolled — so a cabinet leaderboard screen can just
   poll this on a timer.

Player names are free text (not tied to the real Teams-based
leaderboard/roster) — typed at score submission, sanitized, and grouped
case-insensitively. So the arcade games are wide open to anyone typing a
name at the cabinet; they don't require an account or the "real"
leaderboard identity.

**Networking implication for the cabinet**: since verification is entirely
server-side (seed issuance, heartbeats, replay), the cabinet machine needs
a live network path to the FastAPI server for the whole session, not just
at score-submit time — an offline/disconnected cabinet can still let
someone play locally, but session start and score posting will fail
without connectivity.
