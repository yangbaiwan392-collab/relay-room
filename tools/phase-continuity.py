#!/usr/bin/env python3
"""Per-domain-block continuity for the final run."""
import sqlite3
import sys
from pathlib import Path

run = Path(sys.argv[1])
conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)
rows = conn.execute(
    "SELECT round, sender, body FROM messages m WHERE kind='utterance' AND seq=("
    "SELECT MAX(seq) FROM messages x WHERE x.run_id=m.run_id AND x.round=m.round "
    "AND x.sender=m.sender AND x.kind='utterance') ORDER BY ts"
).fetchall()
conn.close()

def cjk(s):
    return "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")

for lo in (1, 51, 101, 151):
    phase = [r for r in rows if lo <= r[0] < lo + 50]
    linked = total = 0
    for i in range(1, len(phase)):
        prev, cur = cjk(phase[i - 1][2]), cjk(phase[i][2])
        if len(prev) < 6 or len(cur) < 6:
            continue
        total += 1
        if any(prev[j:j + 6] in cur for j in range(len(prev) - 5)):
            linked += 1
    print(f"r{lo}-{lo+49}: utts={len(phase)} quoted_prev={linked}/{total} "
          f"({100*linked/max(1,total):.0f}%)")
