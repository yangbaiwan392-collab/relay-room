"""Read-only live dashboard for an agents run. Redraws in place; Ctrl+C to quit.

Reads the run's SQLite bus; never writes. With --run-root it follows the newest run,
so it can be opened before the supervisor starts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CLEAR = "\x1b[2J\x1b[H"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"


def vram() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip().splitlines()[0]
        used, total, util = [x.strip() for x in out.split(",")]
        return f"{used}/{total} MiB ({int(used)/int(total)*100:.0f}%)  util {util}%"
    except Exception:
        return "n/a"


def newest_run(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    cands = [p for p in root.iterdir() if p.is_dir() and (p / "bus.sqlite").is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def load(db_path: Path) -> dict | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        def state(key, default=None):
            row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except Exception:
                return row["value"]
        meta = state("run.meta", {}) or {}
        rnd = int(state("run.round", 0) or 0)
        stopped = bool(state("run.stop", 0))
        utt = conn.execute(
            "SELECT round, sender, body, tokens, latency_ms FROM messages "
            "WHERE kind='utterance' ORDER BY ts DESC LIMIT 12").fetchall()
        evs = conn.execute("SELECT ts, kind, detail FROM events ORDER BY seq DESC LIMIT 6").fetchall()
        tot_tok = conn.execute(
            "SELECT COALESCE(SUM(tokens),0) FROM messages WHERE kind='utterance'").fetchone()[0]
        n_utt = conn.execute("SELECT COUNT(*) FROM messages WHERE kind='utterance'").fetchone()[0]
        per = {}
        for row in conn.execute(
            "SELECT sender, COUNT(*), COALESCE(SUM(tokens),0), COALESCE(AVG(latency_ms),0) "
            "FROM messages WHERE kind='utterance' GROUP BY sender"):
            per[row[0]] = {"n": row[1], "tok": row[2], "lat": round(row[3])}
        conn.close()
        return {"meta": meta, "round": rnd, "stopped": stopped, "utt": utt, "evs": evs,
                "tot_tok": tot_tok, "n_utt": n_utt, "per": per}
    except sqlite3.Error:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--run-root", default=str(Path(__file__).resolve().parent / "runs"))
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    while True:
        run_dir = Path(args.run_dir) if args.run_dir else newest_run(Path(args.run_root))
        if run_dir is None or not (run_dir / "bus.sqlite").is_file():
            print(CLEAR + f"{DIM}waiting for a run under {args.run_root} …{RESET}")
            time.sleep(1.0)
            continue
        data = load(run_dir / "bus.sqlite")
        if data is None:
            time.sleep(0.5)
            continue

        meta, per = data["meta"], data["per"]
        w = min(shutil.get_terminal_size((120, 40)).columns, 160)
        out = [CLEAR]
        out.append(f"{BOLD}{CYAN}agents room — live{RESET}  "
                   f"{DIM}{datetime.now().strftime('%H:%M:%S')}{RESET}   VRAM {vram()}")
        out.append(f"run    : {meta.get('run_id', run_dir.name)}   mode={meta.get('mode', '?')}   "
                   f"rounds={meta.get('rounds', '?')}   "
                   f"{'STOPPED' if data['stopped'] else 'running'}")
        topic = str(meta.get("topic", ""))
        out.append(f"topic  : {topic[:w-9]}{'…' if len(topic) > w-9 else ''}")
        out.append(f"progress: round {data['round']}   utterances {data['n_utt']}   "
                   f"tokens {data['tot_tok']}")
        out.append("─" * min(w, 110))
        out.append(f"{BOLD}{'speaker':10s} {'model':22s} {'n':>3s} {'tokens':>7s} {'avg ms':>7s}{RESET}")
        for s in meta.get("roster", []):
            name = s.get("name", "?")
            p = per.get(name, {"n": 0, "tok": 0, "lat": 0})
            out.append(f"{name:10s} {str(s.get('model', '?'))[:22]:22s} "
                       f"{p['n']:3d} {p['tok']:7d} {p['lat']:7d}")
        out.append("─" * min(w, 110))
        out.append(f"{BOLD}latest{RESET}")
        for row in reversed(data["utt"]):
            body = str(row["body"]).replace("\n", " ")
            out.append(f"  r{row['round']:<2d} [{str(row['sender']):8s}] {body[:w-24]}"
                       f"{DIM} ({row['tokens']}t {row['latency_ms']}ms){RESET}")
        out.append(f"{BOLD}events{RESET}")
        for e in reversed(data["evs"]):
            out.append(f"  {DIM}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{RESET} "
                       f"{e['kind']}: {str(e['detail'])[:w-30]}")
        out.append(f"{DIM}Ctrl+C to quit · {run_dir / 'bus.sqlite'}{RESET}")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(); sys.exit(0)
