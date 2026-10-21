#!/usr/bin/env python3
"""Progress + problem probe for a long run: round, counts, dupes, artifacts, switches."""
import collections
import sqlite3
import sys
from pathlib import Path

run = Path(sys.argv[1])
conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row


def state(key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


rnd = state("run.round")
topic = state("run.topic")
block = state("run.block")
used = conn.execute("SELECT COUNT(*) FROM messages WHERE kind='utterance' AND seq=0").fetchone()[0]
latest = conn.execute(
    "SELECT round, sender, body, tokens, latency_ms FROM messages m WHERE kind='utterance' "
    "AND seq=(SELECT MAX(seq) FROM messages x WHERE x.run_id=m.run_id AND x.round=m.round "
    "AND x.sender=m.sender AND x.kind='utterance') ORDER BY round DESC, ts DESC LIMIT 40"
).fetchall()
allbodies = [r["body"] for r in latest]
dups = [b for b, c in collections.Counter(allbodies).items() if c > 1]
echo = sum(1 for b in allbodies if "你上一轮说过" in b)
brack = sum(1 for b in allbodies if any(c in b for c in "『』「」"))
meta = sum(1 for b in allbodies if "注：" in b)
empty = sum(1 for b in allbodies if b.startswith("(empty"))
switches = conn.execute("SELECT COUNT(*) FROM events WHERE kind='topic_switch'").fetchone()[0]
dedups = conn.execute("SELECT COUNT(*) FROM events WHERE kind='dedup_request'").fetchone()[0]
print(f"{run.name}: round={rnd} block={block} utterances={used} switches={switches} dedups={dedups}")
print(f"  topic: {str(topic)[:60]}")
print(f"  last40: dups={len(dups)} echo={echo} brackets={brack} meta={meta} empty={empty}")
lats = [r["latency_ms"] for r in latest if r["latency_ms"]]
if lats:
    print(f"  latency(ms): avg={sum(lats)/len(lats):.0f} max={max(lats)}")
print("  last 2 rounds:")
for r in list(latest)[:8]:
    print(f"    r{r['round']} [{r['sender']}] {r['body'][:70]}")
