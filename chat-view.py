"""Chat-style live view of an agents room. Read-only; follows the newest run.

    python chat-view.py [--run-dir DIR] [--lines 40] [--interval 1.0]

Shows the dialogue as a chat (round headers, coloured speakers, wrapped text), with a
live status line for who is speaking. Ctrl+C to quit; the run keeps going.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import textwrap
import time
import unicodedata
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CLEAR = "\x1b[2J\x1b[H"
COLORS = ["\x1b[36m", "\x1b[33m", "\x1b[35m", "\x1b[32m", "\x1b[34m", "\x1b[91m", "\x1b[96m"]


def dwidth(s: str) -> int:
    """Display width: CJK characters count as two columns."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dwidth(s))


def wrap_display(text: str, width: int) -> list[str]:
    lines, cur, curw = [], "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if curw + w > width and cur:
            lines.append(cur)
            cur, curw = ch, w
        else:
            cur += ch
            curw += w
    if cur:
        lines.append(cur)
    return lines or [""]


def newest(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    cands = [p for p in root.iterdir() if p.is_dir() and (p / "bus.sqlite").is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--run-root", default=str(HERE / "runs"))
    ap.add_argument("--lines", type=int, default=36)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    colour = {}
    while True:
        run = Path(args.run_dir) if args.run_dir else newest(Path(args.run_root))
        if not run or not (run / "bus.sqlite").is_file():
            print(CLEAR + f"{DIM}waiting for a run under {args.run_root} …{RESET}")
            time.sleep(1.0)
            continue
        try:
            conn = sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True, timeout=5)
            meta = state(conn, "run.meta", {}) or {}
            rnd = int(state(conn, "run.round", 0) or 0)
            rounds = int(state(conn, "run.rounds", 0) or 0)
            stopped = bool(state(conn, "run.stop", 0))
            topic = str(meta.get("topic", ""))
            rows = conn.execute(
                "SELECT round, sender, body, tokens, latency_ms FROM messages m "
                "WHERE kind='utterance' AND seq = (SELECT MAX(seq) FROM messages x "
                " WHERE x.run_id=m.run_id AND x.round=m.round AND x.sender=m.sender "
                " AND x.kind='utterance') ORDER BY ts DESC LIMIT ?", (args.lines,)
            ).fetchall()
            statuses = {}
            for s in meta.get("roster", []):
                statuses[s["name"]] = state(conn, f"status:{s['name']}", "?")
            conn.close()
        except sqlite3.Error:
            time.sleep(0.5)
            continue

        w = min(shutil.get_terminal_size((110, 50)).columns, 150)
        state_text = "已结束" if stopped else "\x1b[32m对话中" + RESET
        out = [CLEAR]
        out.append(f"{BOLD}\x1b[95m● 模型聊天室{RESET}  "
                   f"{DIM}{datetime.now().strftime('%H:%M:%S')}{RESET}  "
                   f"第 {rnd}/{rounds} 轮  {state_text}")
        out.append(f"{DIM}话题: {topic[:w-8]}{'…' if len(topic) > w-8 else ''}{RESET}")
        out.append(DIM + "─" * min(w, 100) + RESET)

        shown_round = None
        for r, sender, body, tokens, lat in reversed(rows):
            if r != shown_round:
                out.append(f"{DIM}── 第 {r} 轮 " + "─" * max(3, min(w, 90) - 12) + RESET)
                shown_round = r
            if sender not in colour:
                colour[sender] = COLORS[len(colour) % len(COLORS)]
            name = sender
            wrapped = wrap_display(body.replace("\n", " "), width=max(20, w - 14)) or [""]
            out.append(f"{colour[sender]}{BOLD}{pad(name, 8)}{RESET} {wrapped[0]}")
            for cont in wrapped[1:]:
                out.append(" " * 9 + cont)
            out.append(f"{DIM}{'':9}{tokens:>3d} 字位  {lat:>5d} 毫秒{RESET}")

        out.append(DIM + "─" * min(w, 100) + RESET)
        bits = []
        labels = {"speaking": "\x1b[32m✎发言中", "waiting": DIM + "·等待",
                  "idle": DIM + "○空闲", "revising": "\x1b[33m↻改写中"}
        marks = {"speaking": "\x1b[32m✎", "waiting": DIM + "·",
                 "idle": DIM + "○", "revising": "\x1b[33m↻"}
        for name, st in statuses.items():
            bits.append(f"{marks.get(st, '?')}{RESET} {name}:{labels.get(st, st)}")
        out.append("  ".join(bits))
        out.append(f"{DIM}Ctrl+C 退出（对话会继续跑） · {run.name}{RESET}")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
