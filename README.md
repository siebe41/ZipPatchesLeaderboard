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
backfill pages are described further down, and the Flappy Duck side game has its
own section.

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
```

To wipe the board and start over, which is the honest option when the scores already there
are not worth sorting through:

```
python tools/flappy_admin.py clear                      # every run, asks first
python tools/flappy_admin.py clear --player "Some Name" # one player
```

On the server that script is not available, because the compose file mounts `./app` and
`./data` and nothing else. The deletion itself lives in `flappy.clear_board()` for that
reason, so it can be reached from inside the container:

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
imports it. Sound effects are synthesised in the browser with WebAudio, so there are no audio
files to ship, and the game starts muted.

No build step, no bundler, no npm, no CDN, and no new Python dependencies. Deploy is still
upload files and restart.

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
