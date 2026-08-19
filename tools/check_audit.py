"""Prove the retroactive audit clears forged runs and keeps played ones.

The board only counts verified runs, and every run recorded before this release
is unverified, so audit_legacy_runs() decides which existing scores survive a
restart. That makes it worth testing against a database shaped like the real
one: a mix of honest runs, a run whose trace was computed offline, a run whose
trace does not reproduce its score, and runs with nothing left to check.

Usage:
    python tools\\check_audit.py
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, HERE)

DB = os.path.join(tempfile.gettempdir(), "flappy-audit", "audit-check.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
if os.path.exists(DB):
    os.remove(DB)
os.environ["ZS_BUFFER_DB"] = DB

import flappy  # noqa: E402
from flappy_bot import bot_trace, long_played_trace, played_trace  # noqa: E402



FAILED = []


def check(name, got, want):
    if got == want:
        print("  ok    %s" % name)
    else:
        print("  FAIL  %s: got %r, wanted %r" % (name, got, want))
        FAILED.append(name)


def insert(player, seed, flaps, score=None, trace=True, encoding="legacy"):
    """Write a row in the shape the previous release wrote them.

    The encoding matters more than it looks. The shipped release stored the
    trace twice, because clean_trace() returned json.dumps(list) and the insert
    called json.dumps() on that string again. A fixture that writes a plain JSON
    array is testing a shape no real row has, which is how the first version of
    this audit passed every test here and still cleared nothing on the real
    board. "legacy" is what is actually in the database today.
    """
    sim = flappy.replay(seed, flaps)
    row_score = sim.score if score is None else score
    if not trace:
        stored = None
    elif encoding == "legacy":
        stored = json.dumps(json.dumps(flaps))
    else:
        stored = json.dumps(flaps)
    flappy._write(
        "INSERT INTO flappy_runs "
        "(player, player_key, score, seed, duration_ms, flaps, created_at, verified) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (player, flappy.name_key(player), row_score, seed, sim.duration_ms(),
         stored, flappy._utc_stamp()),
    )
    return row_score


def flags_of(player):
    rows = flappy._rows(
        "SELECT verified, flags FROM flappy_runs WHERE player_key = ?",
        (flappy.name_key(player),),
    )
    return rows[0]["verified"], json.loads(rows[0]["flags"] or "null")


def main():
    rng = random.Random(20260819)

    # A bot with no timing noise, which is what an offline solver produces.
    forged_seed = 399
    forged = bot_trace(forged_seed)
    forged_score = insert("Offline Solver", forged_seed, forged)
    if forged_score < 20:
        print("fixture is wrong: the bot needs a long run to be judgeable")
        return 1

    # Two runs shaped like hands: jittered timing and imperfect aim.
    played = []
    for i in range(2):
        seed = 382 + i
        trace = long_played_trace(seed, rng)
        played.append(insert("Player %d" % i, seed, trace))

    print("fixture: forged run scored %d, honest runs scored %s"
          % (forged_score, played))
    if min(played) < 5:
        print("fixture is wrong: the honest runs are too short to be a real test")
        return 1

    # A trace that does not reproduce the score it was recorded with.
    liar_seed = 77
    liar_trace = long_played_trace(liar_seed, rng)
    insert("Score Editor", liar_seed, liar_trace, score=999)

    # Runs there is nothing left to judge: pruned, and never flapped.
    insert("Pruned Long Ago", 12, played_trace(12, rng),
           score=14, trace=False)
    insert("Never Started", 13, [])

    # A score with an empty trace still stored next to it. Pruning empties the
    # column rather than writing "[]", so this is a number that was typed.
    insert("Made It Up", 12345, [], score=250)

    # The same forged run written in the encoding the current code uses, so both
    # shapes are covered rather than whichever one happens to be in the database.
    insert("Solver Current Shape", forged_seed, forged, encoding="current")

    print("\naudit")
    result = flappy.audit_legacy_runs()
    check("every row judged", result["checked"], 8)
    check("nothing left unjudged",
          flappy._rows("SELECT COUNT(*) AS n FROM flappy_runs WHERE flags IS NULL")[0]["n"], 0)

    print("\nverdicts")
    verified, flags = flags_of("Offline Solver")
    check("computed run does not count", verified, 0)
    check("computed run says why", "machine_timing" in (flags or []), True)

    verified, flags = flags_of("Solver Current Shape")
    check("the same run in the current encoding is also caught", verified, 0)

    for i in range(2):
        verified, flags = flags_of("Player %d" % i)
        check("played run %d still counts" % i, verified, 1)
        check("played run %d is clean" % i, flags, [])

    verified, flags = flags_of("Score Editor")
    check("edited score does not count", verified, 0)
    check("edited score says why", flags, ["replay_mismatch"])

    verified, flags = flags_of("Pruned Long Ago")
    check("pruned run is kept", verified, 1)
    check("pruned run is marked", flags, ["legacy_no_trace"])

    verified, _ = flags_of("Never Started")
    check("empty run is kept", verified, 1)

    verified, flags = flags_of("Made It Up")
    check("a score with no inputs does not count", verified, 0)
    check("and it says why", flags, ["scored_without_flapping"])

    print("\nboard")
    names = [r["player"] for r in flappy.board_rows("alltime")]
    check("board excludes the solver", "Offline Solver" in names, False)
    check("board excludes the editor", "Score Editor" in names, False)
    check("board excludes the made up score", "Made It Up" in names, False)
    check("board keeps the players", sorted(n for n in names if n.startswith("Player")),
          ["Player 0", "Player 1"])

    print("\nrerun is a no-op")
    again = flappy.audit_legacy_runs()
    check("nothing re-judged", again["checked"], 0)

    print("\nclearing the board")
    # Reachable from inside the container, where tools/ does not exist, so it is
    # worth proving the module level entry point rather than only the script.
    before = len(flappy._rows("SELECT id FROM flappy_runs"))
    result = flappy.clear_board("Player 0")
    check("clearing one player deletes only that player", result["deleted"], 1)
    check("the rest are still there",
          len(flappy._rows("SELECT id FROM flappy_runs")), before - 1)
    check("an unknown name deletes nothing",
          flappy.clear_board("Nobody Here")["deleted"], 0)

    result = flappy.clear_board()
    check("clearing everything empties the board", result["deleted"], before - 1)
    check("no runs left", flappy._rows("SELECT id FROM flappy_runs"), [])
    check("no sessions left", flappy._rows("SELECT id FROM flappy_sessions"), [])
    check("the board reads empty rather than erroring",
          flappy.board_rows("alltime"), [])

    print()
    if FAILED:
        print("%d check(s) failed" % len(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
