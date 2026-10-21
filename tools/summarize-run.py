#!/usr/bin/env python3
"""Summarize problems in a run: empties, bracket residue, dedup/bridge/angle activity."""
import collections
import json
import sqlite3
import sys
from pathlib import Path

run = Path(sys.argv[1])
conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)
bodies = [r[0] for r in conn.execute(
    "SELECT body FROM messages WHERE kind='utterance' AND seq=0")]
ev = collections.Counter(k for (k,) in conn.execute("SELECT kind FROM events"))
switches = []
for (d,) in conn.execute("SELECT detail FROM events WHERE kind='topic_switch'"):
    switches.append(d)
conn.close()
empty = sum(1 for b in bodies if b.strip() in ("(empty reply)", "") or b.startswith("(posted:"))
brack = sum(1 for b in bodies if any(c in b for c in "『』「」[]"))
ascii_words = sum(1 for b in bodies if any(ch.isascii() and ch.isalpha() for ch in b))
print(json.dumps({
    "run": run.name,
    "utterances_seq0": len(bodies),
    "empty_or_posted": empty,
    "bracket_residue": brack,
    "contains_ascii_word": ascii_words,
    "events": dict(ev),
    "switches": len(switches),
    "dedup_per_100_rounds": round(100 * ev.get("dedup_request", 0) / max(1, len(bodies) / 4), 1),
}, ensure_ascii=False, indent=2))
