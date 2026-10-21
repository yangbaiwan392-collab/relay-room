"""SQLite WAL bus with per-speaker memory, a shared blackboard, and key/value state."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ts REAL NOT NULL, round INTEGER NOT NULL,
    seq INTEGER DEFAULT 0, sender TEXT NOT NULL, recipient TEXT NOT NULL, kind TEXT NOT NULL,
    body TEXT NOT NULL, reply_to TEXT, tokens INTEGER DEFAULT 0, latency_ms INTEGER DEFAULT 0,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_msg_run_round ON messages(run_id, round);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, speaker TEXT NOT NULL,
    round INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem ON memory(run_id, speaker);
CREATE TABLE IF NOT EXISTS board (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, round INTEGER NOT NULL,
    author TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL
);
"""


class Bus:
    def __init__(self, db: Path | str, *, timeout: float = 10.0):
        self.path = Path(db)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=timeout, check_same_thread=False)
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=10000"):
            self.conn.execute(f"PRAGMA {pragma}")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------- messages ----------------
    def send(self, row: dict) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages (id, run_id, ts, round, seq, sender, recipient, kind, "
            "body, reply_to, tokens, latency_ms, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["run_id"], row["ts"], row["round"], row.get("seq", 0),
             row["sender"], row["recipient"], row["kind"], row["body"], row.get("reply_to"),
             row.get("tokens", 0), row.get("latency_ms", 0), row.get("meta", "{}")),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def utterances(self, run_id: str, rnd: int | None = None, *, seq: int | None = 0) -> list[dict]:
        sql = "SELECT round, seq, sender, body, tokens, latency_ms FROM messages WHERE run_id=? AND kind='utterance'"
        args: list = [run_id]
        if rnd is not None:
            sql += " AND round=?"
            args.append(int(rnd))
        if seq is not None:
            sql += " AND seq=?"
            args.append(int(seq))
        if seq == 0:
            sql += " AND COALESCE(meta,'') NOT LIKE '%\"superseded\": true%'"
        sql += " ORDER BY ts"
        return [{"round": r[0], "seq": r[1], "sender": r[2], "body": r[3],
                 "tokens": r[4], "latency_ms": r[5]} for r in self.conn.execute(sql, args)]

    def latest_utterances(self, run_id: str, rnd: int | None = None) -> list[dict]:
        """One current row per speaker per round (revisions replace, never duplicate)."""
        sql = ("SELECT round, MAX(seq), sender, body, tokens, latency_ms FROM messages "
               "WHERE run_id=? AND kind='utterance'")
        args: list = [run_id]
        if rnd is not None:
            sql += " AND round=?"
            args.append(int(rnd))
        sql += " GROUP BY round, sender ORDER BY round, MIN(ts)"
        return [{"round": r[0], "seq": r[1], "sender": r[2], "body": r[3],
                 "tokens": r[4], "latency_ms": r[5]}
                for r in self.conn.execute(sql, args)]

    def supersede(self, run_id: str, rnd: int, sender: str, keep_seq: int) -> int:
        """Mark older versions of a turn as superseded so every view shows one reply."""
        cur = self.conn.execute(
            "UPDATE messages SET meta=? WHERE run_id=? AND round=? AND sender=? "
            "AND kind='utterance' AND seq < ?",
            ('{"superseded": true}', run_id, int(rnd), sender, int(keep_seq)))
        self.conn.commit()
        return cur.rowcount

    def transcript(self, run_id: str, *, limit: int = 12, upto_round: int | None = None,
                   exclude: str | None = None, start_round: int | None = None) -> list[dict]:
        sql = "SELECT round, seq, sender, body FROM messages WHERE run_id=? AND kind='utterance'"
        args: list = [run_id]
        if upto_round is not None:
            sql += " AND round < ?"
            args.append(int(upto_round))
        if start_round is not None:
            sql += " AND round >= ?"
            args.append(int(start_round))
        if exclude:
            sql += " AND sender != ?"
            args.append(exclude)
        sql += " AND COALESCE(meta,'') NOT LIKE '%\"superseded\": true%'"
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        return [{"round": r[0], "seq": r[1], "sender": r[2], "body": r[3]}
                for r in reversed(self.conn.execute(sql, args).fetchall())]

    def has(self, run_id: str, rnd: int, sender: str, seq: int = 0) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM messages WHERE run_id=? AND round=? AND seq=? AND sender=? "
            "AND kind='utterance' LIMIT 1", (run_id, int(rnd), int(seq), sender)).fetchone() is not None

    def wait_all(self, run_id: str, rnd: int, speakers: list[str], *, timeout: float,
                 seq: int = 0) -> list[str]:
        deadline = time.time() + timeout
        while True:
            missing = [s for s in speakers if not self.has(run_id, rnd, s, seq)]
            if not missing or time.time() >= deadline:
                return missing
            time.sleep(0.25)

    def tokens(self, run_id: str) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(tokens),0) FROM messages WHERE run_id=? AND kind='utterance'",
            (run_id,)).fetchone()[0]

    def count(self, run_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE run_id=? AND kind='utterance'",
            (run_id,)).fetchone()[0]

    # ---------------- memory / blackboard ----------------
    def add_memory(self, run_id: str, speaker: str, rnd: int, kind: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO memory (run_id, speaker, round, kind, text, ts) VALUES (?,?,?,?,?,?)",
            (run_id, speaker, int(rnd), kind, text, time.time()))
        self.conn.commit()

    def memory_of(self, run_id: str, speaker: str, *, limit: int = 6) -> list[dict]:
        rows = self.conn.execute(
            "SELECT round, kind, text FROM memory WHERE run_id=? AND speaker=? "
            "ORDER BY id DESC LIMIT ?", (run_id, speaker, int(limit))).fetchall()
        return [{"round": r[0], "kind": r[1], "text": r[2]} for r in reversed(rows)]

    def add_board(self, run_id: str, rnd: int, author: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO board (run_id, round, author, text, ts) VALUES (?,?,?,?,?)",
            (run_id, int(rnd), author, text, time.time()))
        self.conn.commit()

    def board(self, run_id: str, *, limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT round, author, text FROM board WHERE run_id=? ORDER BY id DESC LIMIT ?",
            (run_id, int(limit))).fetchall()
        return [{"round": r[0], "author": r[1], "text": r[2]} for r in reversed(rows)]

    def board_all(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, round, author, text FROM board WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [{"id": r[0], "round": r[1], "author": r[2], "text": r[3]} for r in rows]

    def board_prune(self, run_id: str, keep_ids: list[int]) -> int:
        keep = ",".join(str(int(i)) for i in keep_ids) or "0"
        cur = self.conn.execute(
            f"DELETE FROM board WHERE run_id=? AND id NOT IN ({keep})", (run_id,))
        self.conn.commit()
        return cur.rowcount

    def memory_all(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, speaker, round, kind, text FROM memory WHERE run_id=? ORDER BY id",
            (run_id,)).fetchall()
        return [{"id": r[0], "speaker": r[1], "round": r[2], "kind": r[3], "text": r[4]}
                for r in rows]

    def memory_prune(self, run_id: str, keep_ids: list[int]) -> int:
        keep = ",".join(str(int(i)) for i in keep_ids) or "0"
        cur = self.conn.execute(
            f"DELETE FROM memory WHERE run_id=? AND id NOT IN ({keep})", (run_id,))
        self.conn.commit()
        return cur.rowcount

    # ---------------- state / events ----------------
    def set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO state (key, value, ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (key, json.dumps(value), time.time()))
        self.conn.commit()

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]

    def event(self, kind: str, detail: str) -> None:
        self.conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
                          (time.time(), kind, detail))
        self.conn.commit()

    def event_count(self, kind: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE kind=?",
                                 (kind,)).fetchone()[0]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
