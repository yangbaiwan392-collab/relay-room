#!/usr/bin/env python3
"""Continuity metric: does each turn actually quote/carry the previous turn?

For every utterance in chronological order, check whether it contains a 6-char CJK
substring of the immediately preceding utterance. Also reports basic stats.
"""
import sqlite3
import sys
from pathlib import Path

for arg in sys.argv[1:]:
    run = Path(arg)
    conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT round, sender, body FROM messages m WHERE kind='utterance' AND seq=("
        "SELECT MAX(seq) FROM messages x WHERE x.run_id=m.run_id AND x.round=m.round "
        "AND x.sender=m.sender AND x.kind='utterance') ORDER BY ts"
    ).fetchall()
    conn.close()
    def cjk(s):
        return "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
    linked = total = 0
    respond = 0
    lens = []
    for i in range(1, len(rows)):
        prev, cur = cjk(rows[i - 1][2]), cjk(rows[i][2])
        if len(prev) < 6 or len(cur) < 6:
            continue
        total += 1
        if any(prev[j:j + 6] in cur for j in range(len(prev) - 5)):
            linked += 1
        if len({prev[j:j + 2] for j in range(len(prev) - 1)}
               & {cur[j:j + 2] for j in range(len(cur) - 1)}) >= 4:
            respond += 1
    lens = [len(r[2]) for r in rows]
    print(f"{run.name}: utts={len(rows)}  quoted={linked}/{total} "
          f"({100*linked/max(1,total):.0f}%)  respond={respond}/{total} "
          f"({100*respond/max(1,total):.0f}%)  avg_len={sum(lens)/max(1,len(lens)):.0f}")
