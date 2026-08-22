"""Look at PatchMan runs, and take one off the board or put it back.

The automatic checks are deliberately conservative, and on this game they are
looser still: Flappy Duck had 800 measured human runs to calibrate against and
PatchMan has none yet. So they will let through a patient forgery and, more
rarely, hold back an honest run that looks odd. Both need a person, and a
person needs to see the evidence rather than a verdict. Every command here
prints what the judgement was based on.

Nothing writes to leaderboard.json or history.json, and nothing here touches
any table that is not prefixed patchman_.

    python tools\\patchman_admin.py board
    python tools\\patchman_admin.py suspects
    python tools\\patchman_admin.py show 41
    python tools\\patchman_admin.py player "Andrew Siebert"
    python tools\\patchman_admin.py void 41 --why "posted from a script"
    python tools\\patchman_admin.py restore 41
    python tools\\patchman_admin.py recheck

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
    import patchman
    if os.path.abspath(patchman.BUFFER_DB) != os.path.abspath(db_path):
        sys.exit("patchman.py is already bound to %s, not %s"
                 % (patchman.BUFFER_DB, db_path))
    return patchman


def fmt_flags(raw):
    if raw is None:
        return "not judged"
    try:
        flags = json.loads(raw)
    except ValueError:
        return raw
    return ", ".join(flags) if flags else "clean"


def cmd_board(pm, args):
    rows = pm.board_rows(args.view)[:args.limit]
    if not rows:
        print("no runs count in that view yet")
        return
    print("%-4s %-26s %7s %6s %8s %9s  %s"
          % ("#", "player", "score", "level", "patches", "seconds", "when"))
    for r in rows:
        print("%-4d %-26s %7d %6d %8d %9.1f  %s"
              % (r["rank"], r["player"][:26], r["score"], r["level"] or 0,
                 r["patches"] or 0, r["duration_ms"] / 1000.0, r["created_at"]))


def cmd_suspects(pm, args):
    rows = pm._rows(
        "SELECT id, player, score, level, duration_ms, turn_count, created_at, "
        "flags FROM patchman_runs WHERE verified = 0 "
        "ORDER BY score DESC, id ASC LIMIT ?", (args.limit,))
    if not rows:
        print("nothing has been held back")
        return
    print("%-6s %-22s %7s %6s %8s %7s  %-19s %s"
          % ("id", "player", "score", "level", "seconds", "turns", "when", "why"))
    for r in rows:
        print("%-6d %-22s %7d %6s %8.1f %7s  %-19s %s"
              % (r["id"], r["player"][:22], r["score"],
                 r["level"] if r["level"] is not None else "?",
                 r["duration_ms"] / 1000.0,
                 r["turn_count"] if r["turn_count"] is not None else "?",
                 r["created_at"], fmt_flags(r["flags"])))


def cmd_show(pm, args):
    rows = pm._rows("SELECT * FROM patchman_runs WHERE id = ?", (args.id,))
    if not rows:
        sys.exit("no run with id %d" % args.id)
    r = rows[0]
    print("run %d" % r["id"])
    print("  player      %s" % r["player"])
    print("  score       %d" % r["score"])
    print("  reached     level %s, %s patches deployed"
          % (r["level"], r["patches"]))
    print("  duration    %.1f seconds" % (r["duration_ms"] / 1000.0))
    print("  recorded    %s" % r["created_at"])
    print("  counts      %s" % ("yes" if r["verified"] else "no"))
    print("  judgement   %s" % fmt_flags(r["flags"]))
    if r["elapsed_ms"] is not None:
        print("  real time   %.1f seconds between the seed and the submission"
              % (r["elapsed_ms"] / 1000.0))

    trace, stored = pm.decode_trace(r["turns"])
    if not stored:
        print("  inputs      the trace has been pruned, so there is nothing to replay")
        return
    if trace is None:
        print("  inputs      stored, but not readable as a trace")
        return
    if not trace:
        print("  inputs      none, so this run never turned")
        return

    sim = pm.replay(r["seed"], trace)
    print("  inputs      %d turns" % len(trace))
    print("  replay      %d points, level %d, %d patches, %.1f seconds%s"
          % (sim.score, sim.level, sim.total_patches,
             sim.duration_ms() / 1000.0,
             "" if sim.score == r["score"] else "  <-- does not match the stored score"))
    print("              %d vulnerabilities patched" % sim.vulns_patched)

    # The trace packs a tick and a direction into one integer, so the gaps
    # between turns are the player's hand. Everything below is that hand,
    # described.
    ticks = [t // 4 for t in trace]
    stats = pm.interval_stats(trace)
    seconds = sim.duration_ms() / 1000.0
    print()
    print("  what the timing looks like")
    if seconds > 0:
        print("    turns per second    %.2f  (a hand sustains under %.1f)"
              % (len(trace) / seconds, pm.MAX_TURNS_PER_SEC))
    print("    same exact gap      %.3f of gaps land on one value  (over %.2f "
          "is called machine timing, and only once there are %d gaps to judge)"
          % (stats["modal_share"], pm.MODAL_SHARE_LIMIT, pm.MODAL_MIN_INTERVALS))
    print("    gaps under %d ticks  %d  (allowed %d)"
          % (pm.MIN_HUMAN_GAP_TICKS, stats["short"], pm.MAX_SHORT_GAPS))

    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    if gaps:
        counts = {}
        for g in gaps:
            counts[g] = counts.get(g, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        print("    most common gaps    "
              + ", ".join("%d ticks x%d" % (g, n) for g, n in top))

    # A solver turns on tile centres, so its choices cluster on the four
    # directions differently from a hand steering through a maze. This is not a
    # check, only something a person can eyeball.
    names = ("up", "left", "down", "right")
    used = [0, 0, 0, 0]
    for t in trace:
        used[t % 4] += 1
    print("    directions          "
          + ", ".join("%s %d" % (names[i], used[i]) for i in range(4)))


def cmd_player(pm, args):
    key = pm.name_key(args.name)
    rows = pm._rows(
        "SELECT id, score, level, patches, duration_ms, created_at, verified, "
        "flags FROM patchman_runs WHERE player_key = ? ORDER BY id", (key,))
    if not rows:
        sys.exit("no runs for %s" % args.name)
    print("%-6s %7s %6s %8s %8s  %-19s %-7s %s"
          % ("id", "score", "level", "patches", "seconds", "when", "counts", "why"))
    for r in rows:
        print("%-6d %7d %6s %8s %8.1f  %-19s %-7s %s"
              % (r["id"], r["score"],
                 r["level"] if r["level"] is not None else "?",
                 r["patches"] if r["patches"] is not None else "?",
                 r["duration_ms"] / 1000.0, r["created_at"],
                 "yes" if r["verified"] else "no", fmt_flags(r["flags"])))


def _set_verified(pm, run_id, verified, note):
    rows = pm._rows("SELECT player, score, flags FROM patchman_runs WHERE id = ?",
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
    pm._write("UPDATE patchman_runs SET verified = ?, flags = ? WHERE id = ?",
              (verified, json.dumps(flags), run_id))
    print("run %d (%s, %d points) %s"
          % (run_id, r["player"], r["score"],
             "now counts" if verified else "no longer counts"))


def cmd_void(pm, args):
    _set_verified(pm, args.id, 0, args.why or "removed after review")


def cmd_restore(pm, args):
    _set_verified(pm, args.id, 1, args.why or "restored after review")


def cmd_recheck(pm, args):
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
    rows = pm._rows("SELECT id, score, seed, turns, flags FROM patchman_runs ORDER BY id")
    changed = 0
    for r in rows:
        try:
            flags = json.loads(r["flags"]) if r["flags"] else []
        except ValueError:
            flags = []
        if any(f.startswith("by hand:") for f in flags):
            continue  # a person decided this one, leave it alone

        kept = [f for f in flags if f in pm.CLOCK_FLAGS]
        verified, judged = pm.audit_run(r["seed"], r["score"], r["turns"])
        new_flags = kept + [f for f in judged if f not in kept]
        if kept:
            verified = 0
        if new_flags != flags:
            changed += 1
            pm._write("UPDATE patchman_runs SET verified = ?, flags = ? WHERE id = ?",
                      (verified, json.dumps(new_flags), r["id"]))
            print("run %-6d %s -> %s" % (r["id"], fmt_flags(r["flags"]),
                                         fmt_flags(json.dumps(new_flags))))
    print("looked at %d runs, changed %d" % (len(rows), changed))


def cmd_clear(pm, args):
    """Wipe the board.

    Nothing else in this tool destroys data, so this asks first unless told not
    to. The deletion itself lives in patchman.clear_board(), so the same
    statement runs whether it is invoked from here or inside the container,
    where this script does not exist.
    """
    if args.player:
        key = pm.name_key(args.player)
        rows = pm._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM patchman_runs "
                        "WHERE player_key = ?", (key,))
        what = 'every run by "%s"' % args.player
    else:
        rows = pm._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM patchman_runs")
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

    result = pm.clear_board(args.player or None)
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
