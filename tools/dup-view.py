#!/usr/bin/env python3
"""Compare seq=0 view vs latest-per-(round,sender) view for duplicate rates."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sqlite3


def rows(run, latest=False):
    conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)
    if latest:
        q = ("SELECT round, sender, body, seq FROM messages m WHERE kind='utterance' AND seq = "
             "(SELECT MAX(seq) FROM messages x WHERE x.run_id=m.run_id AND x.round=m.round "
             " AND x.sender=m.sender AND x.kind='utterance') ORDER BY ts")
    else:
        q = "SELECT round, sender, body, seq FROM messages WHERE kind='utterance' AND seq=0 ORDER BY ts"
    out = conn.execute(q).fetchall()
    conn.close()
    return out


def stats(rs):
    texts = [r[2] for r in rs]
    counts = {}
    for t in texts:
        counts[t] = counts.get(t, 0) + 1
    dup = sum(c - 1 for c in counts.values() if c > 1)
    return {"utts": len(texts), "exact_dup_rate": round(dup / len(texts), 3),
            "distinct_1": round(len(counts) / len(texts), 3),
            "revisions": sum(1 for r in rs if r[3] == 1)}


for arg in sys.argv[1:]:
    run = Path(arg)
    print(f"{run.name}  seq0={stats(rows(run))}  latest={stats(rows(run, True))}")
