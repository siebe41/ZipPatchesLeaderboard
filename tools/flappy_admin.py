"""Look at Flappy Duck runs, and take one off the board or put it back.

The automatic checks are deliberately conservative, so they will let through a
patient forgery and, much more rarely, hold back an honest run that looks odd.
Both need a person, and a person needs to be able to see the evidence rather
than a verdict. Every command here prints what the judgement was based on.

Nothing writes to leaderboard.json or history.json, and nothing here touches
any table that is not prefixed flappy_.

    python tools\\flappy_admin.py board
    python tools\\flappy_admin.py suspects
    python tools\\flappy_admin.py show 41
    python tools\\flappy_admin.py player "Andrew Siebert"
    python tools\\flappy_admin.py void 41 --why "posted from a script"
    python tools\\flappy_admin.py restore 41
    python tools\\flappy_admin.py recheck
    python tools\\flappy_admin.py info

This is a workstation tool. A deploy is an upload of app/, so tools/ never
reaches the server: point it at a copy of the database with --db, or set
ZS_BUFFER_DB. The default is the path the container uses, so on a dev box you
almost always want --db. What has to be done on the server instead lives in
flappy.py itself, as GET /flappy/api/health and flappy.clear_board().
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
    import flappy
    if os.path.abspath(flappy.BUFFER_DB) != os.path.abspath(db_path):
        sys.exit("flappy.py is already bound to %s, not %s"
                 % (flappy.BUFFER_DB, db_path))
    return flappy


def fmt_flags(raw):
    if raw is None:
        return "not judged"
    try:
        flags = json.loads(raw)
    except ValueError:
        return raw
    return ", ".join(flags) if flags else "clean"


def cmd_board(fp, args):
    rows = fp.board_rows(args.view)[:args.limit]
    if not rows:
        print("no runs count in that view yet")
        return
    print("%-4s %-28s %6s %9s  %s" % ("#", "player", "score", "seconds", "when"))
    for r in rows:
        print("%-4d %-28s %6d %9.1f  %s"
              % (r["rank"], r["player"][:28], r["score"],
                 r["duration_ms"] / 1000.0, r["created_at"]))


def cmd_suspects(fp, args):
    rows = fp._rows(
        "SELECT id, player, score, duration_ms, flap_count, created_at, flags "
        "FROM flappy_runs WHERE verified = 0 "
        "ORDER BY score DESC, id ASC LIMIT ?", (args.limit,))
    if not rows:
        print("nothing has been held back")
        return
    print("%-6s %-24s %6s %8s %7s  %-19s %s"
          % ("id", "player", "score", "seconds", "flaps", "when", "why"))
    for r in rows:
        print("%-6d %-24s %6d %8.1f %7s  %-19s %s"
              % (r["id"], r["player"][:24], r["score"], r["duration_ms"] / 1000.0,
                 r["flap_count"] if r["flap_count"] is not None else "?",
                 r["created_at"], fmt_flags(r["flags"])))


def cmd_show(fp, args):
    rows = fp._rows("SELECT * FROM flappy_runs WHERE id = ?", (args.id,))
    if not rows:
        sys.exit("no run with id %d" % args.id)
    r = rows[0]
    print("run %d" % r["id"])
    print("  player      %s" % r["player"])
    print("  score       %d" % r["score"])
    print("  duration    %.1f seconds" % (r["duration_ms"] / 1000.0))
    print("  recorded    %s" % r["created_at"])
    print("  counts      %s" % ("yes" if r["verified"] else "no"))
    print("  judgement   %s" % fmt_flags(r["flags"]))
    if r["elapsed_ms"] is not None:
        print("  real time   %.1f seconds between the seed and the submission"
              % (r["elapsed_ms"] / 1000.0))

    trace, stored = fp.decode_trace(r["flaps"])
    if not stored:
        print("  inputs      the trace has been pruned, so there is nothing to replay")
        return
    if trace is None:
        print("  inputs      stored, but not readable as a trace")
        return
    if not trace:
        print("  inputs      none, so this run never flapped")
        return

    sim = fp.replay(r["seed"], trace)
    print("  inputs      %d" % len(trace))
    print("  replay      %d patches over %.1f seconds%s"
          % (sim.score, sim.duration_ms() / 1000.0,
             "" if sim.score == r["score"] else "  <-- does not match the stored score"))

    stats = fp.interval_stats(trace)
    seconds = sim.duration_ms() / 1000.0
    print()
    print("  what the timing looks like")
    if seconds > 0:
        print("    taps per second     %.2f  (a hand sustains under %.1f)"
              % (len(trace) / seconds, fp.MAX_FLAPS_PER_SEC))
    print("    same exact gap      %.3f of gaps land on one value  (played runs "
          "measured up to 0.20, computed ones from 0.32)" % stats["modal_share"])
    print("    gaps under %d ticks  %d  (allowed %d)"
          % (fp.MIN_HUMAN_GAP_TICKS, stats["short"], fp.MAX_SHORT_GAPS))

    gaps = [trace[i + 1] - trace[i] for i in range(len(trace) - 1)]
    if gaps:
        counts = {}
        for g in gaps:
            counts[g] = counts.get(g, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        print("    most common gaps    "
              + ", ".join("%d ticks x%d" % (g, n) for g, n in top))


def cmd_player(fp, args):
    key = fp.name_key(args.name)
    rows = fp._rows(
        "SELECT id, score, duration_ms, created_at, verified, flags "
        "FROM flappy_runs WHERE player_key = ? ORDER BY id", (key,))
    if not rows:
        sys.exit("no runs for %s" % args.name)
    print("%-6s %6s %8s  %-19s %-7s %s"
          % ("id", "score", "seconds", "when", "counts", "why"))
    for r in rows:
        print("%-6d %6d %8.1f  %-19s %-7s %s"
              % (r["id"], r["score"], r["duration_ms"] / 1000.0, r["created_at"],
                 "yes" if r["verified"] else "no", fmt_flags(r["flags"])))


def _set_verified(fp, run_id, verified, note):
    rows = fp._rows("SELECT player, score, flags FROM flappy_runs WHERE id = ?", (run_id,))
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
    fp._write("UPDATE flappy_runs SET verified = ?, flags = ? WHERE id = ?",
              (verified, json.dumps(flags), run_id))
    print("run %d (%s, %d patches) %s"
          % (run_id, r["player"], r["score"],
             "now counts" if verified else "no longer counts"))


def cmd_void(fp, args):
    _set_verified(fp, args.id, 0, args.why or "removed after review")


def cmd_restore(fp, args):
    _set_verified(fp, args.id, 1, args.why or "restored after review")


def cmd_recheck(fp, args):
    """Judge every run again from scratch, including ones already judged.

    Worth having when a threshold moves: the automatic pass at startup only
    looks at runs it has never seen.
    """
    rows = fp._rows("SELECT id, score, seed, flaps, flags FROM flappy_runs ORDER BY id")
    changed = 0
    for r in rows:
        try:
            flags = json.loads(r["flags"]) if r["flags"] else []
        except ValueError:
            flags = []
        if any(f.startswith("by hand:") for f in flags):
            continue  # a person decided this one, leave it alone
        verified, new_flags = fp.audit_run(r["seed"], r["score"], r["flaps"])
        if new_flags != flags:
            changed += 1
            fp._write("UPDATE flappy_runs SET verified = ?, flags = ? WHERE id = ?",
                      (verified, json.dumps(new_flags), r["id"]))
            print("run %-6d %s -> %s" % (r["id"], fmt_flags(r["flags"]),
                                         fmt_flags(json.dumps(new_flags))))
    print("looked at %d runs, changed %d" % (len(rows), changed))


def cmd_clear(fp, args):
    """Wipe the board.

    Nothing else in this tool destroys data, so this asks first unless told not
    to. The deletion itself lives in flappy.clear_board(), so the same statement
    runs whether it is invoked from here or inside the container, where this
    script does not exist.
    """
    if args.player:
        key = fp.name_key(args.player)
        rows = fp._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM flappy_runs "
                        "WHERE player_key = ?", (key,))
        what = 'every run by "%s"' % args.player
    else:
        rows = fp._rows("SELECT COUNT(*) AS n, MAX(score) AS best FROM flappy_runs")
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

    result = fp.clear_board(args.player or None)
    print("cleared %d runs" % result["deleted"])


def cmd_info(fp, args):
    """Which database this is, and what is in it."""
    info = fp.db_info()
    tables = info.pop("tables", [])
    for key, value in info.items():
        print("%-24s %s" % (key, value))
    print("%-24s %s" % ("tables", ", ".join(tables) or "(none)"))


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

    p = sub.add_parser("info", help="which database is in use, and what is in it")
    p.set_defaults(fn=cmd_info)

    args = ap.parse_args()
    if not os.path.exists(args.db):
        sys.exit("no database at %s. Pass --db or set ZS_BUFFER_DB." % args.db)
    args.fn(load_module(args.db), args)


if __name__ == "__main__":
    main()
