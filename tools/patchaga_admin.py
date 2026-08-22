"""Look at Patchaga runs, and take one off the board or put it back.

The automatic checks are deliberately conservative, and on this game they are
looser still: Flappy Duck had 800 measured human runs to calibrate against and
Patchaga has none. So they will let through a patient forgery and, more rarely,
hold back an honest run that looks odd. Both need a person, and a person needs
to see the evidence rather than a verdict. Every command here prints what the
judgement was based on.

Patchaga judges a hand differently from the other games, and the difference
shows up throughout this tool. Steering is judged on its timing, but firing is
not, because the client auto-repeats while the button is held and that repeat
lands on the cooldown to the tick every time -- measuring it for regularity
would flag every honest player. Firing is judged on its result instead, which
is the hit rate. So a run's timing evidence below covers steering only, and its
aim is reported separately.

Nothing writes to leaderboard.json or history.json, and nothing here touches
any table that is not prefixed patchaga_.

    python tools\\patchaga_admin.py board
    python tools\\patchaga_admin.py suspects
    python tools\\patchaga_admin.py show 41
    python tools\\patchaga_admin.py player "Andrew Siebert"
    python tools\\patchaga_admin.py void 41 --why "posted from a script"
    python tools\\patchaga_admin.py restore 41
    python tools\\patchaga_admin.py recheck

Point it at a database with --db, or set ZS_BUFFER_DB. The default is the path
the container uses, so run it inside the container or pass the path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))


def load_module(db_path):
    os.environ["ZS_BUFFER_DB"] = db_path
    import patchaga
    if os.path.abspath(patchaga.BUFFER_DB) != os.path.abspath(db_path):
        sys.exit("patchaga.py is already bound to %s, not %s"
                 % (patchaga.BUFFER_DB, db_path))
    return patchaga


def fmt_flags(raw):
    if raw is None:
        return "not judged"
    try:
        flags = json.loads(raw)
    except ValueError:
        return raw
    return ", ".join(flags) if flags else "clean"


def cmd_board(pg, args):
    rows = pg.board_rows(args.view)[:args.limit]
    if not rows:
        print("no runs count in that view yet")
        return
    print("%-4s %-26s %7s %6s %7s %9s  %s"
          % ("#", "player", "score", "wave", "bugs", "seconds", "when"))
    for r in rows:
        print("%-4d %-26s %7d %6d %7d %9.1f  %s"
              % (r["rank"], r["player"][:26], r["score"], r["wave"] or 0,
                 r["bugs"] or 0, r["duration_ms"] / 1000.0, r["created_at"]))


def cmd_suspects(pg, args):
    rows = pg._rows(
        "SELECT id, player, score, wave, duration_ms, input_count, created_at, "
        "flags FROM patchaga_runs WHERE verified = 0 "
        "ORDER BY score DESC, id ASC LIMIT ?", (args.limit,))
    if not rows:
        print("nothing has been held back")
        return
    print("%-6s %-22s %7s %6s %8s %7s  %-19s %s"
          % ("id", "player", "score", "wave", "seconds", "inputs", "when", "why"))
    for r in rows:
        print("%-6d %-22s %7d %6s %8.1f %7s  %-19s %s"
              % (r["id"], r["player"][:22], r["score"],
                 r["wave"] if r["wave"] is not None else "?",
                 r["duration_ms"] / 1000.0,
                 r["input_count"] if r["input_count"] is not None else "?",
                 r["created_at"], fmt_flags(r["flags"])))


def cmd_show(pg, args):
    rows = pg._rows("SELECT * FROM patchaga_runs WHERE id = ?", (args.id,))
    if not rows:
        sys.exit("no run with id %d" % args.id)
    r = rows[0]
    print("run %d" % r["id"])
    print("  player      %s" % r["player"])
    print("  score       %d" % r["score"])
    print("  reached     wave %s, %s bugs patched" % (r["wave"], r["bugs"]))
    print("  duration    %.1f seconds" % (r["duration_ms"] / 1000.0))
    print("  recorded    %s" % r["created_at"])
    print("  counts      %s" % ("yes" if r["verified"] else "no"))
    print("  judgement   %s" % fmt_flags(r["flags"]))
    if r["elapsed_ms"] is not None:
        print("  real time   %.1f seconds between the seed and the submission"
              % (r["elapsed_ms"] / 1000.0))

    trace, stored = pg.decode_trace(r["inputs"])
    if not stored:
        print("  inputs      the trace has been pruned, so there is nothing to replay")
        return
    if trace is None:
        print("  inputs      stored, but not readable as a trace")
        return
    if not trace:
        print("  inputs      none, so this run never moved and never fired")
        return

    sim = pg.replay(r["seed"], trace)
    print("  inputs      %d presses" % len(trace))
    print("  replay      %d points, wave %d, %d bugs, %.1f seconds%s"
          % (sim.score, sim.wave, sim.bugs_patched, sim.duration_ms() / 1000.0,
             "" if sim.score == r["score"] else "  <-- does not match the stored score"))
    print("              %d waves cleared, %d captures, %d rescues"
          % (sim.waves_cleared, sim.forks, sim.rescues))

    # The trace packs a tick and an action into one integer, so the gaps between
    # presses are the player's hand. Everything below is that hand, described.
    steering = pg.steering_codes(trace)
    stats = pg.interval_stats(trace)
    seconds = sim.duration_ms() / 1000.0
    print()
    print("  what the timing looks like  (steering only, see the note above)")
    if seconds > 0:
        print("    steers per second   %.2f  (a hand sustains under %.1f)"
              % (len(steering) / seconds, pg.MAX_STEERS_PER_SEC))
    print("    same exact gap      %.3f of gaps land on one value  (over %.2f "
          "is called machine timing, and only once there are %d gaps to judge)"
          % (stats["modal_share"], pg.MODAL_SHARE_LIMIT, pg.MODAL_MIN_INTERVALS))
    print("    gaps under %d ticks  %d  (allowed %d)"
          % (pg.MIN_HUMAN_GAP_TICKS, stats["short"], pg.MAX_SHORT_GAPS))

    ticks = [c // 4 for c in steering]
    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    if gaps:
        counts = {}
        for g in gaps:
            counts[g] = counts.get(g, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        print("    most common gaps    "
              + ", ".join("%d ticks x%d" % (g, n) for g, n in top))

    print()
    print("  what the aim looks like")
    rate = pg.accuracy_of(sim)
    if rate is None:
        print("    hit rate            %d shots is too few to judge  (needs %d)"
              % (sim.shots_fired, pg.ACCURACY_MIN_SHOTS))
    else:
        print("    hit rate            %.0f%% of %d shots hit  (over %.0f%% is "
              "called inhuman; the reference bot lands 38-75%%)"
              % (rate * 100, sim.shots_fired, pg.MAX_HUMAN_ACCURACY * 100))

    # How the presses divide up. Not a check, only something a person can
    # eyeball: a solver that steers on tick boundaries tends to leave far more
    # neutrals than a hand, which mostly holds one direction and then the other.
    names = ("left", "right", "neutral", "fire")
    used = [0, 0, 0, 0]
    for c in trace:
        used[c % 4] += 1
    print()
    print("  how the presses divide up")
    print("    " + ", ".join("%s %d" % (names[i], used[i]) for i in range(4)))


def cmd_player(pg, args):
    key = pg.name_key(args.name)
    rows = pg._rows(
        "SELECT id, score, wave, bugs, duration_ms, created_at, verified, "
        "flags FROM patchaga_runs WHERE player_key = ? ORDER BY id", (key,))
    if not rows:
        sys.exit("no runs for %s" % args.name)
    print("%-6s %7s %6s %7s %8s  %-19s %-7s %s"
          % ("id", "score", "wave", "bugs", "seconds", "when", "counts", "why"))
    for r in rows:
        print("%-6d %7d %6s %7s %8.1f  %-19s %-7s %s"
              % (r["id"], r["score"],
                 r["wave"] if r["wave"] is not None else "?",
                 r["bugs"] if r["bugs"] is not None else "?",
                 r["duration_ms"] / 1000.0, r["created_at"],
                 "yes" if r["verified"] else "no", fmt_flags(r["flags"])))


def _set_verified(pg, run_id, verified, note):
    rows = pg._rows("SELECT player, score, flags FROM patchaga_runs WHERE id = ?",
                    (run_id,))
    if not rows:
        sys.exit("no run with id %d" % run_id)
    r = rows[0]
    try:
        flags = json.loads(r["flags"]) if r["flags"] else []
    except ValueError:
        flags = []
    flags = [f for f in flags if not f.startswith("by hand:")]
    if note:
        flags.append("by hand: " + note)
    pg._write("UPDATE patchaga_runs SET verified = ?, flags = ? WHERE id = ?",
              (verified, json.dumps(flags), run_id))
    print("run %d (%s, %d points) %s"
          % (run_id, r["player"], r["score"],
             "now counts" if verified else "no longer counts"))


def cmd_void(pg, args):
    _set_verified(pg, args.id, 0, args.why or "removed after review")


def cmd_restore(pg, args):
    _set_verified(pg, args.id, 1, args.why or "restored after review")


def cmd_recheck(pg, args):
    """Judge every run again from scratch, including ones already judged.

    Worth having when a threshold moves, which on this game is expected: the
    automatic pass at startup only looks at runs it has never seen, so moving a
    threshold otherwise only affects runs posted after the change.

    Only the hand checks are redone. The clock checks were decided against the
    session's heartbeats, which are not kept, so a recheck cannot re-run them
    and instead carries the verdict forward. Without that, rechecking would
    quietly clear every run that was held back for not being played in real
    time, which is the one thing a recheck must never do.
    """
    rows = pg._rows("SELECT id, score, seed, inputs, flags FROM patchaga_runs ORDER BY id")
    changed = 0
    for r in rows:
        try:
            flags = json.loads(r["flags"]) if r["flags"] else []
        except ValueError:
            flags = []
        if any(f.startswith("by hand:") for f in flags):
            continue  # a person decided this one, leave it alone

        kept = [f for f in flags if f in pg.CLOCK_FLAGS]
        verified, judged = pg.audit_run(r["seed"], r["score"], r["inputs"])
        new_flags = kept + [f for f in judged if f not in kept]
        if kept:
            verified = 0
        if new_flags != flags:
            changed += 1
            pg._write("UPDATE patchaga_runs SET verified = ?, flags = ? WHERE id = ?",
                      (verified, json.dumps(new_flags), r["id"]))
            print("run %-6d %s -> %s" % (r["id"], fmt_flags(r["flags"]),
                                         fmt_flags(json.dumps(new_flags))))
    print("looked at %d runs, changed %d" % (len(rows), changed))


def cmd_clear(pg, args):
    """Wipe the board.

    Nothing else in this tool destroys data, so this asks first unless told not
    to. The deletion itself lives in patchaga.clear_board(), so the same
    statement runs whether it is invoked from here or inside the container,
    where this script does not exist.
    """
    if args.player:
        key = pg.name_key(args.player)
        rows = pg._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM patchaga_runs "
                        "WHERE player_key = ?", (key,))
        what = 'every run by "%s"' % args.player
    else:
        rows = pg._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM patchaga_runs")
        what = "every run by everyone"
    n, best = rows[0]["n"], rows[0]["best"]

    if not n:
        print("nothing to clear")
        return

    print("about to delete %d run%s (%s), best score %s"
          % (n, "" if n == 1 else "s", what, best))
    if not args.yes:
        if input("type 'clear' to go ahead: ").strip() != "clear":
            print("left alone")
            return

    result = pg.clear_board(args.player or None)
    print("cleared %d runs" % result["deleted"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("ZS_BUFFER_DB",
                                                   "/home/zipscores_buffer.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("board", help="what the leaderboard shows")
    p.add_argument("view", nargs="?", default="alltime",
                   choices=["alltime", "season", "today", "volume"])
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_board)

    p = sub.add_parser("suspects", help="runs that were held back, and why")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_suspects)

    p = sub.add_parser("show", help="one run in full, with its timing evidence")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("player", help="every run by one player")
    p.add_argument("name")
    p.set_defaults(fn=cmd_player)

    p = sub.add_parser("void", help="take a run off the board")
    p.add_argument("id", type=int)
    p.add_argument("--why", default="")
    p.set_defaults(fn=cmd_void)

    p = sub.add_parser("restore", help="put a held back run onto the board")
    p.add_argument("id", type=int)
    p.add_argument("--why", default="")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("recheck", help="judge every run again, keeping manual decisions")
    p.set_defaults(fn=cmd_recheck)

    p = sub.add_parser("clear", help="delete runs and start the board over")
    p.add_argument("--player", default="", help="only this player, rather than everyone")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(fn=cmd_clear)

    args = ap.parse_args()
    if not os.path.exists(args.db):
        sys.exit("no database at %s. Pass --db or set ZS_BUFFER_DB." % args.db)
    args.fn(load_module(args.db), args)


if __name__ == "__main__":
    main()
