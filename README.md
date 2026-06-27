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
`/history`, `/reset`, `/adjust`, `/`.

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

## Environment variables

| Variable             | Default                      | Purpose                                            |
| -------------------- | ---------------------------- | -------------------------------------------------- |
| `ZS_TIMEZONE`        | `America/Chicago`            | Local TZ for "yesterday" and the daily scheduler   |
| `ZS_BUFFER_DB`       | `/home/zipscores_buffer.db`  | SQLite buffer path (under `./data:/home`, persists) |
| `ZS_COLLECTOR_TOKEN` | `` (empty)                   | If set, `/collect` requires `X-Token` to match     |
| `ZS_RETENTION_DAYS`  | `30`                         | Prune buffered messages older than this many days  |
| `ZS_FINALIZE_HOUR`   | `2`                          | Daily finalize run hour (local time)               |
| `ZS_FINALIZE_MINUTE` | `10`                         | Daily finalize run minute (local time)             |

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

