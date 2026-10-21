#!/usr/bin/env python3
"""Small read-only helpers: python query.py <run_dir> [events|conv|board|notes|round N|summary]"""
import json
import sqlite3
import sys
from pathlib import Path

run = Path(sys.argv[1])
what = sys.argv[2] if len(sys.argv) > 2 else "summary"
db = run / "bus.sqlite"

if what == "summary":
    print(json.dumps(json.loads((run / "summary.json").read_text(encoding="utf-8")), indent=2))
    raise SystemExit
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
if what == "events":
    for k, d in conn.execute("SELECT kind, detail FROM events ORDER BY seq"):
        print(f"  {k}: {d}")
elif what == "conv":
    for k, d in conn.execute("SELECT kind, detail FROM events WHERE kind='convergence'"):
        print(" ", d)
elif what == "board":
    for r, a, t in conn.execute("SELECT round, author, text FROM board ORDER BY id"):
        print(f"  r{r} [{a}] {t}")
elif what == "notes":
    for r, s, k, t in conn.execute("SELECT round, speaker, kind, text FROM memory ORDER BY id"):
        print(f"  r{r} [{s}/{k}] {t}")
elif what.isdigit():
    for s, b in conn.execute("SELECT sender, body FROM messages WHERE round=? AND kind='utterance' "
                             "AND COALESCE(meta,'') NOT LIKE '%\"superseded\": true%' "
                             "ORDER BY ts", (int(what),)):
        print(f"  [{s:9s}] {b}")
else:
    print("usage: query.py <run> [events|conv|board|notes|<round>|summary]")
