"""Wire format for the multi-speaker room.

One row in SQLite = one message. Ids are deterministic so a restarted speaker can
never double-post: id = "<run>:<round>:<speaker>:<kind>[:<seq>]".
"""
from __future__ import annotations

import hashlib
import json
import time

KINDS = ("utterance", "system", "turn", "event", "stop")


def now() -> float:
    return time.time()


def msg_id(run_id: str, rnd: int, sender: str, kind: str, seq: int = 0) -> str:
    raw = f"{run_id}|{rnd}|{sender}|{kind}|{seq}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def make(
    *,
    run_id: str,
    rnd: int,
    sender: str,
    recipient: str = "*",
    kind: str = "utterance",
    body: str = "",
    reply_to: str | None = None,
    tokens: int = 0,
    latency_ms: int = 0,
    seq: int = 0,
    meta: dict | None = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    return {
        "id": msg_id(run_id, rnd, sender, kind, seq),
        "run_id": run_id,
        "ts": now(),
        "round": int(rnd),
        "seq": int(seq),
        "sender": sender,
        "recipient": recipient,
        "kind": kind,
        "body": body,
        "reply_to": reply_to,
        "tokens": int(tokens),
        "latency_ms": int(latency_ms),
        "meta": json.dumps(meta or {}, ensure_ascii=False),
    }


def validate(row: dict) -> None:
    for key in ("id", "run_id", "round", "sender", "recipient", "kind", "body"):
        if key not in row:
            raise ValueError(f"message missing {key!r}")
    if row["kind"] not in KINDS:
        raise ValueError(f"bad kind {row['kind']!r}")
