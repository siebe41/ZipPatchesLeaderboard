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

Run `deploy.ps1` from the project folder. It tars the project (excluding `data`,
`.git`, caches), SCPs it to the NAS, then runs
`docker-compose up -d --build` on the Synology to rebuild and restart the single
container.
