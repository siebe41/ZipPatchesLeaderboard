"""Prove the Teams-message score parser counts real times and only real times.

A message that fails to parse is not just skipped: the finalizer scores that
player as missing, at the day's worst time + 1. So a rule that rejects a legit
score turns a great day into a penalty, which is how 2-second Zip times were
being lost to the old 9-second combined floor.

Pulls clean(), parse_score() and the limits straight out of app/main.py, so it
runs with no FastAPI install and touches no data files.

Usage:
    python tools\\check_parse_score.py
"""
from __future__ import annotations

import ast
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(os.path.dirname(HERE), "app", "main.py")

WANTED_FUNCS = {"clean", "parse_score", "_zs_looks_score_ish"}
WANTED_CONSTS = {"MIN_GAME_SECONDS", "MIN_BARE_SCORE", "MAX_SCORE"}


def load_parser():
    tree = ast.parse(open(MAIN, encoding="utf-8").read())
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED_FUNCS:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANTED_CONSTS for t in node.targets):
            keep.append(node)
    ns = {"re": re, "html": html}
    exec(compile(ast.Module(keep, []), MAIN, "exec"), ns)
    return ns


CASES = [
    # Fast but legit: every one of these used to be rejected and penalized.
    ("2//6", (2, 6)),
    ("2 // 5", (2, 5)),
    ("1//4", (1, 4)),
    ("<p>2//6</p>", (2, 6)),
    ("2//6 new PR!", (2, 6)),
    # Ordinary scores still parse.
    ("12//30", (12, 30)),
    ("<p>12 // 30 <at id=\"0\">Andrew</at> beat that</p>", (12, 30)),
    ("12//30&nbsp;", (12, 30)),
    # A 0 is not a time.
    ("0//0", None),
    ("0//12", None),
    ("12//0", None),
    ("12//1000001", None),
    # Bare numbers score Zip only, so small chat replies still must not count.
    ("2", None),
    ("8", None),
    ("45", (45, 0)),
    # Separate lines must not fuse into one bigger number.
    ("<p>12//30</p><p>5 seconds faster than yesterday</p>", (12, 30)),
    ("<p>12//30<br>2nd try lol</p>", (12, 30)),
    # A Teams reply quotes the message it answers; score the reply, not the quote.
    ('<blockquote itemtype="http://schema.skype.com/Reply" itemid="1">'
     '<strong itemprop="mri">Gavin Speed</strong><p itemprop="copy">11//13</p>'
     '</blockquote><p>9//20</p>', (9, 20)),
    ("No scores here", None),
]


def main():
    ns = load_parser()
    failures = 0
    for message, expected in CASES:
        got = ns["parse_score"](message)
        if got != expected:
            failures += 1
            print(f"FAIL {message!r}: expected {expected}, got {got}")
    # The finalizer drops a line before parse_score sees it unless it looks
    # score-ish, so a fast score has to clear that gate too.
    for message in ("2//6", "<p>2//6</p>"):
        if not ns["_zs_looks_score_ish"](ns["clean"](message)):
            failures += 1
            print(f"FAIL {message!r} is filtered out before parsing")
    total = len(CASES) + 2
    print(f"{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
