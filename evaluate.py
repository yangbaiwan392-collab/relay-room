#!/usr/bin/env python3
"""Score agents runs on diversity / stability / repetition / mention responsiveness.

Usage: python evaluate.py runs/<run> [runs/<run> ...]
Embeds utterances with the local embedding model when available (falls back to hashed
trigrams and labels the method).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from itertools import combinations
from pathlib import Path

import embeddings


def load(run_dir: Path) -> dict:
    conn = sqlite3.connect(f"file:{run_dir / 'bus.sqlite'}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT round, seq, sender, body FROM messages WHERE kind='utterance' ORDER BY ts"
    ).fetchall()
    board = conn.execute("SELECT COUNT(*) FROM board").fetchone()[0]
    mem = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    conn.close()
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    return {"utts": [{"round": r[0], "seq": r[1], "sender": r[2], "body": r[3]} for r in rows],
            "board": board, "mem": mem, "summary": summary}


ACTION = re.compile(r"\b(i will|i'll|let me|step \d|first,|measure|check|run|build|write|"
                    r"compare|benchmark|instrument|record)\b", re.I)
CONCRETE = re.compile(r"(\d+(\.\d+)?\s*(%|ms|s|gb|gib|mb|tokens?|rounds?|models?|files?|rows?)"
                      r"|\.(csv|json|md|docx|xlsx|py|ts|sh)\b|"
                      r"\b(report|table|script|metric|benchmark|dataset|log)\b)", re.I)


def strict_handoff(utts: list[dict]) -> tuple[int, int, float | None]:
    """Stricter contract: the reply must be an action AND carry a number/unit/artifact."""
    pattern = re.compile(r"@([a-z0-9_]{2,20})\s*[:：]\s*(.+)", re.I)
    index = {(u["round"], u["sender"]): i for i, u in enumerate(utts)}
    total = ok = 0
    for i, u in enumerate(utts):
        for m in pattern.finditer(u["body"]):
            target = m.group(1).lower()
            if target == u["sender"]:
                continue
            j = index.get((u["round"] + 1, target))
            if j is None:
                continue
            total += 1
            reply = utts[j]["body"]
            if ACTION.search(reply) and CONCRETE.search(reply):
                ok += 1
    return total, ok, (round(ok / total, 3) if total else None)


def handoff_compliance(utts: list[dict]) -> tuple[int, int, float | None]:
    """Handoffs = '@name: request'. Compliant reply = next-round action of the target."""
    pattern = re.compile(r"@([a-z0-9_]{2,20})\s*[:：]\s*(.+)", re.I)
    index = {(u["round"], u["sender"]): i for i, u in enumerate(utts)}
    total = compliant = 0
    for i, u in enumerate(utts):
        for m in pattern.finditer(u["body"]):
            target = m.group(1).lower()
            if target == u["sender"]:          # self-mention is noise, not a handoff
                continue
            j = index.get((u["round"] + 1, target))
            if j is None:
                continue
            total += 1
            reply = utts[j]["body"]
            if ACTION.search(reply) or len(reply) >= 120:
                compliant += 1
    rate = round(compliant / total, 3) if total else None
    return total, compliant, rate


def mention_score(utts: list[dict], vecs: list[list[float]]) -> tuple[int, float | None]:
    """Mean (cos(mention, target reply next round) - cos(mention, target's other turns))."""
    pattern = re.compile(r"@([a-z0-9_]{2,20})\b", re.I)
    index = {(u["round"], u["sender"]): i for i, u in enumerate(utts)}
    by_sender: dict[str, list[int]] = {}
    for i, u in enumerate(utts):
        by_sender.setdefault(u["sender"], []).append(i)
    deltas, n = [], 0
    for i, u in enumerate(utts):
        for target in {t.lower() for t in pattern.findall(u["body"])}:
            if target == u["sender"]:
                continue
            j = index.get((u["round"] + 1, target))
            if j is None:
                continue
            n += 1
            directed = embeddings.cosine(vecs[i], vecs[j])
            others = [embeddings.cosine(vecs[i], vecs[k]) for k in by_sender.get(target, [])
                      if k != j]
            if others:
                deltas.append(directed - sum(others) / len(others))
    return n, (round(sum(deltas) / len(deltas), 4) if deltas else None)


def distinct_n(texts: list[str], n: int = 2) -> float:
    grams, total = set(), 0
    for t in texts:
        words = t.lower().split()
        for i in range(len(words) - n + 1):
            grams.add(tuple(words[i:i + n]))
            total += 1
    return round(len(grams) / total, 3) if total else 0.0


def main() -> None:
    runs = [Path(a) for a in sys.argv[1:]]
    model = "spk-embed"
    for run in runs:
        d = load(run)
        if not d["utts"]:
            print(f"{run.name}: no utterances"); continue
        texts = [u["body"] for u in d["utts"]]
        vecs, method = embeddings.embed(texts, model)

        by_round: dict[int, list[int]] = {}
        for i, u in enumerate(d["utts"]):
            by_round.setdefault(u["round"], []).append(i)

        within = []
        for _, idxs in sorted(by_round.items()):
            v = [vecs[i] for i in idxs]
            if len(v) >= 2:
                sims = [embeddings.cosine(v[a], v[b]) for a, b in combinations(range(len(v)), 2)]
                within.append(sum(sims) / len(sims))

        ror = []
        rounds = sorted(by_round)
        for r in rounds[1:]:
            cur, prev = by_round[r], by_round[r - 1]
            n = min(len(cur), len(prev))
            sims = [embeddings.cosine(vecs[cur[i]], vecs[prev[i]]) for i in range(n)]
            ror.append(sum(sims) / n)

        by_sender: dict[str, list[int]] = {}
        for i, u in enumerate(d["utts"]):
            by_sender.setdefault(u["sender"], []).append(i)
        self_sims = []
        for idxs in by_sender.values():
            for a, b in zip(idxs, idxs[1:]):
                self_sims.append(embeddings.cosine(vecs[a], vecs[b]))

        def mean(xs):
            return round(sum(xs) / len(xs), 4) if xs else None

        d1 = len({t.strip().lower() for t in texts}) / len(texts)
        s = d["summary"]
        n_mention, mention_delta = mention_score(d["utts"], vecs)
        ho_total, ho_ok, ho_rate = handoff_compliance(d["utts"])
        st_total, st_ok, st_rate = strict_handoff(d["utts"])
        out = {
            "run": run.name,
            "method": method,
            "utts": len(texts),
            "rounds": s.get("rounds_done", len(rounds)),
            "tokens": s.get("tokens", sum(len(t.split()) for t in texts)),
            "wall_s": s.get("wall_s"),
            "stopped": s.get("stopped", ""),
            "board": d["board"],
            "memory": d["mem"],
            "within_round_cos": mean(within),
            "round_over_round_cos": mean(ror),
            "self_repeat_cos": mean(self_sims),
            "distinct_1": round(d1, 3),
            "distinct_2": distinct_n(texts),
            "avg_chars": round(sum(len(t) for t in texts) / len(texts)),
            "mentions": n_mention,
            "mention_lift": mention_delta,
            "handoffs": ho_total,
            "handoff_rate": ho_rate,
            "strict_handoffs": st_total,
            "strict_rate": st_rate,
        }
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
