"""Supervisor: spawns one speaker process per model, drives rounds, routes mentions,
enforces stop conditions (rounds / tokens / wall / embedding convergence), writes summary.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import embeddings
import conductor
from bus import Bus

HERE = Path(__file__).resolve().parent
PY = sys.executable
MENTION = re.compile(r"@([a-z0-9_]{2,20})\b", re.I)
HANDOFF = re.compile(r"@([a-z0-9_]{2,20})\s*[:：]\s*(.+)", re.I)
ACTION = re.compile(r"\b(i will|i'll|let me|step \d|first,|measure|check|run|build|write|"
                    r"compare|benchmark|instrument|record)\b", re.I)
CONCRETE = re.compile(r"(\d+(\.\d+)?\s*(%|ms|s|gb|gib|mb|tokens?|rounds?|models?|files?|rows?)"
                      r"|\.(csv|json|md|docx|xlsx|py|ts|sh)\b|"
                      r"\b(report|table|script|metric|benchmark|dataset|log)\b)", re.I)


ANALOGY = re.compile(r"像|好像|如同|好比|就像|仿佛|似的|一样|好比说|打个比方")


def topic_violations(body: str, words: list[str]) -> list[str]:
    """Avoid-words that appear as TOPIC content, not as an analogy.

    '想学新东西，就像搬家一样累' mentions 搬家 inside a simile and must not be flagged;
    '先买家具再买床垫' is the old topic leaking and must be.
    """
    hits: list[str] = []
    for w in words:
        start = 0
        while True:
            idx = body.find(w, start)
            if idx < 0:
                break
            window = body[max(0, idx - 20): idx + len(w) + 20]
            if not ANALOGY.search(window):
                hits.append(w)
                break
            start = idx + len(w)
    return hits


NAME_PAT = re.compile(r"(?:嘿|喂|诶|哎|嗨)\s*([\u4e00-\u9fff]{2,3})[，,：:]")
NAME_WHITELIST = {"大家", "哥们", "朋友", "兄弟", "姑娘", "小子", "老铁", "各位", "同学", "看官"}
ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{1,}")
ASCII_OK = {"AI", "App", "APP", "LED", "GB", "MB", "TB", "ms", "WiFi", "Wi-Fi", "USB",
            "CPU", "GPU", "5G", "4G", "PC", "OK", "PDF", "Excel", "Word", "PPT", "IP",
            "DNA", "RNA", "CT", "MRI", "BMI", "kg", "cm", "km", "ml", "mg"}


def ngram_repeats(body: str, history: list[str], n: int = 6, need: int = 3) -> str:
    """Return a Chinese n-gram that this speaker has already used `need` times before."""
    cur = "".join(ch for ch in body if "\u4e00" <= ch <= "\u9fff")
    best, best_count = "", 0
    for i in range(len(cur) - n + 1):
        g = cur[i:i + n]
        count = sum(1 for h in history if g in h)
        if count >= need and count > best_count:
            best, best_count = g, count
    return best


def phrase_violations(body: str, locks: dict, history: list[str],
                      n: int = 8, need: int = 2) -> list[str]:
    """Speaker-level habits: banned phrases (and their variants), forced analogies,
    ASCII words, length, and repeated n-grams."""
    bad: list[str] = []
    for p in (locks.get("banned_phrases") or []):
        if not p:
            continue
        variants = {p}
        for pre in ("这对", "这种", "这个", "那种", "那", "这"):
            if p.startswith(pre) and len(p) > len(pre) + 4:
                variants.add(p[len(pre):])
        if any(v in body for v in variants):
            bad.append(f"禁句「{p}」")
    if locks.get("no_analogy") and ANALOGY.search(body):
        bad.append("又在打比方")
    words = [w for w in ASCII_WORD.findall(body) if w not in ASCII_OK]
    if words:
        bad.append("夹了英文词 " + ",".join(words[:2]))
    limit = int(locks.get("max_chars", 0) or 0)
    if limit and len(body) > limit * 1.3:
        bad.append(f"太长（{len(body)} 字 > {limit}）")
    g = ngram_repeats(body, history, n=n, need=need)
    if g:
        bad.append(f"重复片段「{g}」")
    return bad


def unknown_names(body: str, known: set[str]) -> list[str]:
    """Names the model invented or imported from outside the room roster."""
    return sorted({m for m in NAME_PAT.findall(body)
                   if m not in known and m not in NAME_WHITELIST})


def strict_reply(text: str) -> bool:
    return bool(ACTION.search(text) and CONCRETE.search(text))


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


class Supervisor:
    def __init__(self, config_path: Path, run_id: str | None = None):
        self.cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
        rc = self.cfg["run"]
        self.run_id = run_id or f"{rc.get('name', 'room')}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.dir = HERE / "runs" / self.run_id
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)
        self.bus = Bus(self.dir / "bus.sqlite")
        self.speakers = self.cfg["speakers"]
        self.names = [s["name"] for s in self.speakers]
        self.procs: dict[str, subprocess.Popen] = {}
        self.restarts = {n: 0 for n in self.names}
        self.rc = rc
        self.missing: dict[int, list[str]] = {}
        self.metrics: list[dict] = []
        self.sampler = None

    # ---------------- setup ----------------
    def banner(self) -> None:
        meta = {
            "run_id": self.run_id, "topic": self.rc["topic"], "mode": self.rc["mode"],
            "rounds": self.rc["rounds"],
            "roster": [{"name": s["name"], "model": s["model"]} for s in self.speakers],
            "features": {k: self.rc.get(k, {}) for k in ("memory", "board", "mentions", "convergence")},
        }
        self.bus.set("run.meta", meta)
        self.bus.set("run.cfg", self.rc)
        self.bus.set("run.names", [s["name"] for s in self.speakers])
        self.bus.set("run.locks", {s["name"]: s["locks"] for s in self.speakers if s.get("locks")})
        for key in ("topic", "mode", "rounds"):
            self.bus.set(f"run.{key}", self.rc[key])
        self.bus.set("run.round", 0)
        self.bus.set("run.stop", 0)
        self.bus.set("run.token_budget", self.rc.get("token_budget", 40000))
        self.bus.event("run_start", f"{self.run_id}: {len(self.speakers)} speakers, "
                                    f"{self.rc['rounds']} rounds, mode={self.rc['mode']}")
        (self.dir / "config.json").write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")

    def start_sampler(self) -> None:
        if not self.rc.get("sample_vram", True):
            return
        try:
            self.sampler = subprocess.Popen(
                [PY, "-u", str(HERE / "sampler.py"), "--out", str(self.dir / "vram.csv"),
                 "--interval", "1", "--duration",
                 str(int(self.rc.get("wall_timeout", 1800)) + 120)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, cwd=str(HERE))
        except OSError:
            self.sampler = None

    def stop_sampler(self) -> None:
        s = self.sampler
        if s and s.poll() is None:
            s.terminate()
            try:
                s.wait(timeout=5)
            except subprocess.TimeoutExpired:
                s.kill()

    def spawn(self, sp: dict) -> None:
        persona = sp["persona"]
        if len(persona) > 400:
            pf = self.dir / f"persona-{sp['name']}.txt"
            pf.write_text(persona, encoding="utf-8")
            persona = str(pf)
        cmd = [PY, "-u", str(HERE / "speaker.py"), "--db", str(self.dir / "bus.sqlite"),
               "--run-id", self.run_id, "--name", sp["name"], "--model", sp["model"],
               "--persona", persona,
               "--max-tokens", str(self.rc.get("max_tokens", 120)),
               "--num-ctx", str(self.rc.get("num_ctx", 4096)),
               "--temperature", str(sp.get("temperature", 0.85)),
               "--history", str(self.rc.get("history", 10)),
               "--model-timeout", str(self.rc.get("model_timeout", 300))]
        out = open(self.dir / "logs" / f"{sp['name']}.log", "a", encoding="utf-8")
        self.procs[sp["name"]] = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(HERE), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.bus.event("spawn", f"{sp['name']} pid={self.procs[sp['name']].pid}")
        log(f"spawned {sp['name']:10s} pid={self.procs[sp['name']].pid} model={sp['model']}")

    def check_alive(self) -> None:
        for sp in self.speakers:
            name = sp["name"]
            p = self.procs.get(name)
            if p is None or p.poll() is None:
                continue
            self.bus.event("speaker_exit", f"{name} rc={p.returncode}")
            if self.restarts[name] >= 3:
                self.bus.event("speaker_dead", f"{name}: giving up")
                continue
            self.restarts[name] += 1
            log(f"!! {name} exited rc={p.returncode}; restart {self.restarts[name]}/3")
            self.spawn(sp)

    # ---------------- rounds ----------------
    def _mention_map(self, rnd: int) -> dict:
        """Scan this round's utterances for @name and build next round's directed map."""
        if not self.rc.get("mentions", {}).get("enabled"):
            return {}
        cap = int(self.rc["mentions"].get("max_per_round", 3))
        out: dict[str, dict] = {}
        for u in self.bus.utterances(self.run_id, rnd, seq=0):
            for target in MENTION.findall(u["body"]):
                target = target.lower()
                if target not in self.names or target == u["sender"] or target in out:
                    continue
                if len(out) >= cap:
                    break
                out[target] = {"round": rnd, "sender": u["sender"],
                               "text": u["body"][:400]}
        if out:
            self.bus.event("mentions", f"r{rnd} -> {list(out)}")
        return out

    def repair_handoffs(self, rnd: int) -> int:
        """Supervisor-enforced contract: a handoff answered without a number/artifact is
        sent back to the same speaker once, with the missing piece named explicitly."""
        mcfg = self.rc.get("mentions", {})
        if not (mcfg.get("enabled") and mcfg.get("repair")):
            return 0
        try:
            max_rep = int(mcfg.get("repair_max", 2))
        except (TypeError, ValueError):
            max_rep = 2
        ups = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        pending: list[tuple[str, str, str]] = []
        for sender, body in ups.items():
            for m in HANDOFF.finditer(body):
                target = m.group(1).lower()
                if target == sender or target not in self.names:
                    continue
                if target in ups and strict_reply(ups[target]):
                    continue
                pending.append((target, sender, m.group(2).strip()))
        repairs = 0
        for target, sender, text in pending[:max_rep]:
            self.bus.set("run.revise", {"round": rnd, "speaker": target,
                                        "sender": sender, "text": text[:400],
                                        "previous": ups.get(target, "")[:400]})
            self.bus.event("repair_request", f"r{rnd} {target} <- {sender}")
            deadline = time.time() + float(self.rc.get("repair_timeout", 90))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, target, seq=1):
                self.check_alive()
                time.sleep(0.3)
            got = self.bus.has(self.run_id, rnd, target, seq=1)
            self.bus.set("run.revise", {})
            if got:
                repairs += 1
                self.bus.event("repair_done", f"r{rnd} {target}")
        if repairs:
            log(f"  repaired {repairs}/{len(pending[:max_rep])} handoff replies")
        return repairs

    def domain_guard(self, rnd: int) -> int:
        """Keep the current domain: no reverting to groceries / cooking / construction."""
        cfg = self.rc.get("domain_guard", {})
        if not cfg.get("enabled"):
            return 0
        domain = self.bus.get("run.domain", "")
        avoid = [w for w in (self.bus.get("run.domain_avoid", []) or []) if str(w).strip()]
        if not domain or not avoid:
            return 0
        cur = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        hits = [(name, [w for w in avoid if w in body]) for name, body in cur.items()
                if len(body.strip()) >= 8]
        hits = [(name, words) for name, words in hits if words]
        repairs = 0
        for name, words in hits[:int(cfg.get("max_per_round", 2))]:
            self.bus.set("run.revise", {"round": rnd, "speaker": name, "reason": "domain",
                                        "previous": cur[name][:400],
                                        "text": "、".join(words) + f"；当前领域：{domain}"})
            self.bus.event("domain_request", f"r{rnd} {name} 跑回日常类比 {words}")
            deadline = time.time() + float(self.rc.get("repair_timeout", 90))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=1):
                self.check_alive()
                time.sleep(0.3)
            got = self.bus.has(self.run_id, rnd, name, seq=1)
            self.bus.set("run.revise", {})
            if got:
                repairs += 1
        if repairs:
            log(f"  domain rewrites: {repairs}")
        return repairs

    def phrase_guard(self, rnd: int) -> int:
        """Turn-level habits: banned phrases, analogy compulsion, ASCII words, repeated n-grams."""
        cfg = self.rc.get("phrase_guard") or {}
        if not cfg.get("enabled"):
            return 0
        locks_all = self.bus.get("run.locks", {}) or {}
        n = int(cfg.get("ngram_n", 6))
        need = int(cfg.get("ngram_repeat", 3))
        cur = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        hist: dict[str, list[str]] = {}
        for u in self.bus.utterances(self.run_id, seq=0):
            if u["round"] < rnd:
                hist.setdefault(u["sender"], []).append(u["body"])
        hits = []
        dynamic = [w.strip() for w in str(self.bus.get("run.banned", "") or "").split(",")
                   if len(w.strip()) >= 2]
        for name, body in cur.items():
            locks = dict(locks_all.get(name) or {})
            if not locks:
                continue
            base_bans = list(locks.get("banned_phrases") or [])
            locks["banned_phrases"] = base_bans + [w for w in dynamic if w not in base_bans]
            if len(body.strip()) < 6:
                continue
            bad = phrase_violations(body, locks, hist.get(name, []), n=n, need=need)
            if bad:
                hits.append((name, bad))
        repairs = 0
        for name, bad in hits[:int(cfg.get("max_per_round", 2))]:
            prev = cur[name]
            seq = 1
            for attempt in range(2):
                self.bus.set("run.revise", {"round": rnd, "speaker": name, "reason": "phrase",
                                            "seq": seq, "previous": prev[:400],
                                            "text": "；".join(bad)})
                self.bus.event("phrase_request", f"r{rnd} {name}#{seq}: {'；'.join(bad)}")
                deadline = time.time() + float(self.rc.get("repair_timeout", 90))
                while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=seq):
                    self.check_alive()
                    time.sleep(0.3)
                got = self.bus.has(self.run_id, rnd, name, seq=seq)
                self.bus.set("run.revise", {})
                if not got:
                    break
                new_body = next((u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=seq)
                                 if u["sender"] == name), "")
                new_bad = phrase_violations(new_body, locks_all.get(name) or {},
                                            hist.get(name, []) + [prev], n=n, need=need)
                if not new_bad:
                    repairs += 1
                    break
                prev, bad = new_body, new_bad
                seq += 1
        if repairs:
            log(f"  phrase rewrites: {repairs}")
        return repairs

    def bridge_check(self, rnd: int) -> int:
        """A topic-bridge turn must reference the tail of the previous block, not just jump."""
        b = self.bus.get("run.bridge") or {}
        if b.get("round") != rnd:
            return 0
        who = b.get("speaker")
        bodies = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        body = bodies.get(who, "")
        prev = self.bus.utterances(self.run_id, rnd - 1, seq=0)
        prev_text = " ".join(u["body"] for u in prev[-2:])
        if not body or not prev_text:
            return 0

        def shingles(s: str) -> set[str]:
            s = "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
            return {s[i:i + 2] for i in range(len(s) - 1)}

        overlap = shingles(body) & shingles(prev_text)
        if len(overlap) >= 2:
            self.bus.event("bridge_ok", f"r{rnd} {who} overlap={len(overlap)}")
            return 0
        self.bus.set("run.revise", {"round": rnd, "speaker": who, "reason": "bridge",
                                    "previous": body[:400],
                                    "text": prev_text[-300:]})
        self.bus.event("bridge_retry", f"r{rnd} {who} overlap={len(overlap)}")
        deadline = time.time() + float(self.rc.get("repair_timeout", 90))
        while time.time() < deadline and not self.bus.has(self.run_id, rnd, who, seq=1):
            self.check_alive()
            time.sleep(0.3)
        got = self.bus.has(self.run_id, rnd, who, seq=1)
        self.bus.set("run.revise", {})
        if got:
            self.bus.event("bridge_done", f"r{rnd} {who}")
        return 1 if got else 0

    def name_guard(self, rnd: int) -> int:
        """A room has a fixed roster; inventing '老张' is a failure, not flavour."""
        cfg = self.rc.get("name_guard", {})
        if not cfg.get("enabled"):
            return 0
        known = set(self.names)
        cur = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        hits = [(name, bad) for name, body in cur.items()
                if (bad := unknown_names(body, known))]
        repairs = 0
        for name, bad in hits[:int(cfg.get("max_per_round", 2))]:
            self.bus.set("run.revise", {"round": rnd, "speaker": name, "reason": "name",
                                        "previous": cur[name][:400],
                                        "text": "、".join(bad)})
            self.bus.event("name_request", f"r{rnd} {name} 用了名单外的名字 {bad}")
            deadline = time.time() + float(self.rc.get("repair_timeout", 90))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=1):
                self.check_alive()
                time.sleep(0.3)
            got = self.bus.has(self.run_id, rnd, name, seq=1)
            self.bus.set("run.revise", {})
            if got:
                repairs += 1
        if repairs:
            log(f"  name rewrites: {repairs}")
        return repairs

    def relay_guard(self, rnd: int, name: str, prev: dict) -> int:
        """A relayed turn must pick up the previous point (quote OR shared keywords)."""
        body = next((u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)
                     if u["sender"] == name), "")
        prev_body = (prev or {}).get("body", "")
        if not body or len(prev_body) < 6:
            return 0
        src = "".join(ch for ch in prev_body if "\u4e00" <= ch <= "\u9fff")
        dst = "".join(ch for ch in body if "\u4e00" <= ch <= "\u9fff")
        quoted = any(src[i:i + 6] in dst for i in range(len(src) - 5))
        shared = len({src[i:i + 2] for i in range(len(src) - 1)}
                     & {dst[i:i + 2] for i in range(len(dst) - 1)})
        mode = str(self.rc.get("relay_check", "quote"))
        ok = quoted if mode == "quote" else (quoted or shared >= 4)
        if ok:
            self.bus.event("relay_ok", f"r{rnd} {name} quote={int(quoted)} shared={shared}")
            return 0
        self.bus.set("run.revise", {"round": rnd, "speaker": name, "reason": "relay", "seq": 1,
                                    "previous": body[:400],
                                    "text": prev_body[:300],
                                    "sender": (prev or {}).get("sender", "")})
        self.bus.event("relay_retry", f"r{rnd} {name}")
        deadline = time.time() + float(self.rc.get("turn_timeout", 150))
        while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=1):
            self.check_alive()
            time.sleep(0.3)
        got = self.bus.has(self.run_id, rnd, name, seq=1)
        self.bus.set("run.revise", {})
        if got:
            self.bus.event("relay_done", f"r{rnd} {name}")
        return 1 if got else 0

    def topic_guard(self, rnd: int) -> int:
        """If a turn drags in words from an earlier topic phase, send it back once."""
        cfg = self.rc.get("topic_guard", {})
        if not cfg.get("enabled"):
            return 0
        plan = self.rc.get("topic_plan") or []
        idx = int(self.bus.get("run.topic_index", 0) or 0)
        if idx <= 0 or idx >= len(plan):
            return 0
        avoid = [w for w in plan[idx].get("avoid", []) if str(w).strip()]
        if not avoid:
            return 0
        cur = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        hits = [(name, topic_violations(body, avoid)) for name, body in cur.items()
                if len(body.strip()) >= 8]
        hits = [(name, words) for name, words in hits if words]
        repairs = 0
        for name, words in hits[:int(cfg.get("max_per_round", 2))]:
            self.bus.set("run.revise", {"round": rnd, "speaker": name, "reason": "topic",
                                        "previous": cur[name][:400], "text": "、".join(words)})
            self.bus.event("topic_request", f"r{rnd} {name} 提到旧话题词 {words}")
            deadline = time.time() + float(self.rc.get("repair_timeout", 90))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=1):
                self.check_alive()
                time.sleep(0.3)
            got = self.bus.has(self.run_id, rnd, name, seq=1)
            self.bus.set("run.revise", {})
            if got:
                repairs += 1
        if repairs:
            log(f"  topic rewrites: {repairs}")
        return repairs

    def dedup_pass(self, rnd: int) -> int:
        """Send a speaker back to rewrite when its turn repeats anything it recently said.

        Compares against the speaker's own last `own_window` turns (not just the previous
        round), because small models settle into a periodic loop rather than an adjacent one.
        Threshold can be tightened per speaker (smaller models paraphrase more loosely).
        """
        cfg = self.rc.get("dedup", {})
        if not cfg.get("enabled") or rnd < 2:
            return 0
        per_thr = cfg.get("per_speaker") or {}
        thr_default = float(cfg.get("threshold", 0.9))
        max_rep = int(cfg.get("max_per_round", 2))
        window = int(cfg.get("own_window", 4))
        model = self.rc.get("convergence", {}).get("model") or None
        cur = {u["sender"]: u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)}
        all_utts = self.bus.utterances(self.run_id, seq=0)
        skel_thr = float(cfg.get("skeleton_threshold", 0.8))
        suspects: list[tuple[str, float, str, str]] = []
        for name, body in cur.items():
            if len(body.strip()) < 12 or body.strip() == "(empty reply)":
                continue
            thr = float(per_thr.get(name, thr_default))
            own = [u["body"] for u in all_utts
                   if u["sender"] == name and u["round"] < rnd and len(u["body"].strip()) >= 12]
            own = own[-window:]
            if not own:
                continue
            vecs, _ = embeddings.embed([body] + own, model)
            best = max(((embeddings.cosine(vecs[0], vecs[i + 1]),
                         embeddings.skeleton_jaccard(body, own[i]), i)
                        for i in range(len(own))), key=lambda t: max(t[0], t[1]))
            sem, skel, idx = best
            if sem >= thr or skel >= skel_thr:
                kind = "semantic" if sem >= thr else "template"
                suspects.append((name, max(sem, skel), own[idx], kind))
        repairs = 0
        for name, sim, worst, kind in suspects[:max_rep]:
            self.bus.set("run.revise", {
                "round": rnd, "speaker": name, "reason": "dedup",
                "previous": worst[:400],
                "check": f"{'句式模板重复' if kind == 'template' else '内容相近'}（相似度 {sim:.2f}）",
            })
            self.bus.event("dedup_request", f"r{rnd} {name} sim={sim:.2f}")
            deadline = time.time() + float(self.rc.get("repair_timeout", 90))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, name, seq=1):
                self.check_alive()
                time.sleep(0.3)
            got = self.bus.has(self.run_id, rnd, name, seq=1)
            self.bus.set("run.revise", {})
            if got:
                repairs += 1
                self.bus.event("dedup_done", f"r{rnd} {name}")
        if repairs:
            log(f"  dedup rewrites: {repairs}")
        return repairs

    def panel_round(self, rnd: int) -> None:
        self.bus.set("run.round", rnd)
        self.bus.set("run.phase", "panel")
        deck = self.rc.get("topic_deck") or {}
        if deck:
            rpt = int(self.rc.get("rounds_per_topic", 50))
            block = (rnd - 1) // rpt
            cur_block = int(self.bus.get("run.block", 0) or 0)
            if rnd == 1:
                self.bus.set("run.topic", self.rc.get("seed_topic", ""))
                self.bus.set("run.prev_topic", "")
                self.bus.set("run.topic_start", 1)
                self.bus.set("run.block", 0)
                self.bus.set("run.deck_used", [])
            elif block != cur_block:
                who = self.names[block % len(self.names)]
                used = self.bus.get("run.deck_used", []) or []
                per = sum(1 for u in used if u["speaker"] == who)
                pool = [t for t in deck.get(who, []) if str(t).strip()]
                topic = pool[per % len(pool)] if pool else f"{who} 的新话题"
                prev = self.bus.get("run.topic", "")
                self.bus.set("run.topic", topic)
                self.bus.set("run.topic_start", rnd)
                self.bus.set("run.digest", [])
                self.bus.set("run.block", block)
                used.append({"round": rnd, "speaker": who, "topic": topic})
                self.bus.set("run.deck_used", used)
                self.bus.set("run.prev_topic", prev)
                self.bus.set("run.bridge", {"round": rnd, "speaker": who,
                                            "to": topic, "from": prev})
                self.bus.event("topic_switch",
                               f"r{rnd} block{block} {who} <- {str(topic)[:32]}")
                self.bus.set("run.topic_note", "")
        plan = self.rc.get("topic_plan") or []
        if plan:
            acc, idx, topic = 0, len(plan) - 1, plan[-1].get("topic", "")
            for i, phase in enumerate(plan):
                acc += int(phase.get("rounds", 0) or 0)
                if rnd <= acc:
                    idx, topic = i, phase.get("topic", "")
                    break
            prev = self.bus.get("run.topic")
            self.bus.set("run.topic", topic)
            self.bus.set("run.topic_index", idx)
            if prev != topic:
                self.bus.event("topic_switch", f"r{rnd} -> {str(topic)[:40]}")
                self.bus.set("run.topic_start", rnd)
                self.bus.set("run.topic_note",
                             "话题刚刚换了：从现在起只聊新话题，不要再提上一个话题里的具体内容"
                             "（比如菜价、买菜、做饭这些细节）。")
                if self.rc.get("reset_digest_on_switch"):
                    self.bus.set("run.digest", [])
            elif idx == 0:
                self.bus.set("run.topic_note", "")
        domains = self.rc.get("domains") or []
        if domains:
            span = int(self.rc.get("domain_rounds", 0) or 0)
            if span > 0:
                dblock = (rnd - 1) // span
                didx = dblock % len(domains)
                switch = self.bus.get("run.domain_block") != dblock
            else:
                acc, didx, dblock = 0, len(domains) - 1, 0
                for i, d in enumerate(domains):
                    acc += int(d.get("rounds", 10) or 0)
                    if rnd <= acc:
                        didx, dblock = i, i
                        break
                switch = self.bus.get("run.domain_index") != didx
            d = domains[didx]
            domain_now = d.get("name", "")
            if switch:
                h = conductor.hint(topic="", rnd=rnd, recent=[], used_angles=[],
                                   dedup_rate=0, roster=self.names, domain=domain_now,
                                   domain_hint=d.get("hint", ""), new_topic=True)
                topic = (h.get("topic") or h.get("angle") or d.get("hint") or domain_now)
                prev = self.bus.get("run.topic", "")
                self.bus.set("run.topic", topic)
                self.bus.set("run.topic_start", rnd)
                self.bus.set("run.digest", [])
                self.bus.set("run.domain_index", didx)
                self.bus.set("run.domain_block", dblock)
                self.bus.set("run.domain", domain_now)
                self.bus.set("run.domain_hint", d.get("hint", ""))
                self.bus.set("run.domain_avoid", d.get("avoid", []))
                self.bus.set("run.bridge", {"round": rnd,
                                            "speaker": self.names[didx % len(self.names)],
                                            "to": topic, "from": prev})
                self.bus.event("domain_switch", f"r{rnd} {domain_now} <- {str(topic)[:36]}")
                log(f"  domain -> {domain_now}: {str(topic)[:40]}")
                if h and "error" not in h:
                    h.pop("topic", None) if h.get("topic") == topic else None
                    self.bus.set("run.conductor", {"round": rnd, "hint": h})
                    self.bus.set("run.used_angles", [h.get("angle", "")])
        angles = self.rc.get("angles") or []
        if angles:
            every = max(1, int(self.rc.get("angle_every", 6)))
            aidx = ((rnd - 1) // every) % len(angles)
            self.bus.set("run.angle", {"round": rnd, "text": angles[aidx]})
        roles = self.rc.get("roles") or []
        if roles:
            assign = {name: roles[(rnd + i) % len(roles)]
                      for i, name in enumerate(self.names)}
            self.bus.set("run.roles", {"round": rnd, "map": assign})
        ccfg = self.rc.get("conductor") or {}
        already = (self.bus.get("run.conductor") or {}).get("round") == rnd
        if ccfg.get("enabled") and not already and (rnd - 1) % max(1, int(ccfg.get("every", 5))) == 0:
            used = self.bus.get("run.used_angles", []) or []
            recent = self.bus.utterances(self.run_id, seq=0)[-8:]
            dedups = self.bus.event_count("dedup_request")
            rate = 100.0 * dedups / max(1, rnd - 1)
            h = conductor.hint(topic=self.bus.get("run.topic", ""), rnd=rnd, recent=recent,
                               used_angles=used, dedup_rate=rate, roster=self.names,
                               domain=self.bus.get("run.domain", ""),
                               domain_hint=self.bus.get("run.domain_hint", ""))
            if h and "error" not in h:
                self.bus.set("run.conductor", {"round": rnd, "hint": h})
                if h.get("banned"):
                    self.bus.set("run.banned", h["banned"])
                if h.get("angle"):
                    self.bus.set("run.used_angles", (used + [h["angle"]])[-40:])
                self.bus.event("conductor", f"r{rnd} angle={h.get('angle', '')[:30]}")
                log(f"  conductor: {h.get('angle', '')[:40]}")
            else:
                self.bus.event("conductor_fail", f"r{rnd} {h.get('error', 'empty')[:60]}")
                log(f"  conductor failed: {h.get('error', 'empty')[:60]}")
        ocfg = self.rc.get("opener_pool", {})
        pools = self.rc.get("opener_pools", {})
        if rnd > int(self.rc.get("opener_max_rounds", 10 ** 9)):
            self.bus.set("run.openers", {})
            self.bus.set("run.opener", {})
            ocfg, pools = {}, {}
        if ocfg.get("enabled"):
            pool = [p for p in ocfg.get("pool", []) if str(p).strip()]
            if pool:
                choices = [p for p in pool if p != getattr(self, "last_opener", None)] or pool
                choice = random.choice(choices)
                self.last_opener = choice
                self.bus.set("run.opener", {"round": rnd,
                                            "speaker": ocfg.get("speaker", ""),
                                            "text": choice})
                self.bus.event("opener", f"r{rnd} {ocfg.get('speaker', '')} <- {choice}")
        if pools:
            if not hasattr(self, "last_openers"):
                self.last_openers = {}
            chosen = {}
            locks_all = self.bus.get("run.locks", {}) or {}
            for who, pool in pools.items():
                pool = [p for p in pool if str(p).strip()]
                ban = (locks_all.get(who) or {}).get("banned_phrases") or []
                pool = [p for p in pool if not any(b in p for b in ban)]
                if not pool:
                    continue
                last = self.last_openers.get(who)
                pick_pool = [p for p in pool if p != last] or pool
                pick = random.choice(pick_pool)
                self.last_openers[who] = pick
                chosen[who] = pick
            if chosen:
                self.bus.set("run.openers", {"round": rnd, "map": chosen})
                self.bus.event("openers", f"r{rnd} " +
                               "; ".join(f"{k}<-{v}" for k, v in chosen.items()))
        if self.rc.get("mode") == "relay":
            start = (rnd - 1) % len(self.names)
            order = self.names[start:] + self.names[:start]
            for i, name in enumerate(order):
                if i == 0:
                    prev_list = self.bus.utterances(self.run_id, seq=0)[-1:]
                else:
                    prev_list = self.bus.utterances(self.run_id, rnd, seq=0)[-1:]
                prev = prev_list[0] if prev_list else {}
                if prev:
                    raw = str(prev.get("body", ""))
                    sents = [s.strip() for s in re.split(r"[。！？!?；;]", raw) if len(s.strip()) >= 4]
                    prev = {"sender": prev.get("sender", ""),
                            "round": prev.get("round", rnd),
                            "body": raw[-100:],
                            "claim": (sents[0] if sents else raw)[:60],
                            "reason": (sents[1] if len(sents) > 1 else "")[:60],
                            "tail": (sents[-1] if sents else "")[:60]}
                self.bus.set("run.relay_prev", prev)
                self.bus.set("run.turn", name)
                deadline = time.time() + float(self.rc.get("turn_timeout", 150))
                while time.time() < deadline and not self.bus.has(self.run_id, rnd, name):
                    self.check_alive()
                    time.sleep(0.3)
                self.bus.set("run.turn", "")
                if not self.bus.has(self.run_id, rnd, name):
                    self.missing.setdefault(rnd, []).append(name)
                    self.bus.event("turn_missing", f"r{rnd} {name}")
                    continue
                self.relay_guard(rnd, name, prev)
        else:
            missing = self.bus.wait_all(self.run_id, rnd, self.names,
                                        timeout=float(self.rc.get("round_timeout", 240)))
            if missing:
                self.missing[rnd] = missing
                self.bus.event("round_missing", f"r{rnd}: {missing}")
        mmap = self._mention_map(rnd)
        self.bus.set("run.mention_map", mmap)
        bridged = self.bridge_check(rnd)
        repaired = self.repair_handoffs(rnd)
        guarded = self.topic_guard(rnd)
        named = self.name_guard(rnd)
        domained = self.domain_guard(rnd)
        phrasing = self.phrase_guard(rnd)
        deduped = self.dedup_pass(rnd)
        ups = self.bus.latest_utterances(self.run_id, rnd)
        log(f"round {rnd:02d}: {len(ups)}/{len(self.names)} spoke, "
            f"{sum(u['tokens'] for u in ups)} tokens"
            + (f", mentions->{list(mmap)}" if mmap else "")
            + (f", bridge={bridged}" if bridged else "")
            + (f", repaired={repaired}" if repaired else "")
            + (f", name={named}" if named else "")
            + (f", domain={domained}" if domained else "")
            + (f", phrase={phrasing}" if phrasing else "")
            + (f", dedup={deduped}" if deduped else ""))

    def serial_round(self, rnd: int) -> None:
        self.bus.set("run.round", rnd)
        self.bus.set("run.phase", "serial")
        queue = list(self.names)
        while queue:
            name = queue.pop(0)
            if self.bus.has(self.run_id, rnd, name):
                continue
            self.bus.set("run.turn", name)
            deadline = time.time() + float(self.rc.get("turn_timeout", 150))
            while time.time() < deadline and not self.bus.has(self.run_id, rnd, name):
                self.check_alive()
                time.sleep(0.3)
            if not self.bus.has(self.run_id, rnd, name):
                self.missing.setdefault(rnd, []).append(name)
                self.bus.event("turn_missing", f"r{rnd} {name}")
                continue
            # mention override: the first @name in the reply speaks next
            body = next((u["body"] for u in self.bus.utterances(self.run_id, rnd, seq=0)
                         if u["sender"] == name), "")
            for target in MENTION.findall(body):
                target = target.lower()
                if target in self.names and target != name and target not in queue \
                        and not self.bus.has(self.run_id, rnd, target):
                    queue.insert(0, target)
                    self.bus.event("mention_route", f"r{rnd} {name}->{target}")
                    break
        self.bus.set("run.turn", "")
        ups = self.bus.utterances(self.run_id, rnd, seq=0)
        log(f"round {rnd:02d}: {len(ups)} utterances (serial)")

    # ---------------- curation / digest ----------------
    def curate(self) -> None:
        """Dedupe and cap blackboard + private memory so they stay signal, not noise."""
        bcfg, mcfg = self.rc.get("board", {}), self.rc.get("memory", {})
        model = self.rc.get("convergence", {}).get("model") or None
        removed = 0
        if bcfg.get("enabled"):
            rows = self.bus.board_all(self.run_id)
            cap = int(bcfg.get("max", 8))
            dup = float(bcfg.get("dup_cosine", 0.92))
            keep: list[int] = []
            if rows:
                vecs, _ = embeddings.embed([r["text"] for r in rows], model)
                for i in range(len(rows) - 1, -1, -1):
                    if len(keep) >= cap:
                        continue
                    if all(embeddings.cosine(vecs[i], vecs[j]) < dup for j in keep):
                        keep.append(i)
            removed += self.bus.board_prune(self.run_id, [rows[i]["id"] for i in keep])
        if mcfg.get("enabled"):
            rows = self.bus.memory_all(self.run_id)
            cap = int(mcfg.get("max_notes", 6))
            dup = float(mcfg.get("dup_cosine", 0.92))
            keep: list[int] = []
            if rows:
                vecs, _ = embeddings.embed([r["text"] for r in rows], model)
                for i in range(len(rows) - 1, -1, -1):
                    if len(keep) >= cap:
                        continue
                    if all(embeddings.cosine(vecs[i], vecs[j]) < dup for j in keep):
                        keep.append(i)
            removed += self.bus.memory_prune(self.run_id, [rows[i]["id"] for i in keep])
        if removed:
            self.bus.event("curate", f"pruned {removed} duplicate/stale notes")

    def update_digest(self, rnd: int) -> None:
        """Extractive per-round digest: one clause per speaker, kept for the next rounds."""
        cfg = self.rc.get("memory", {})
        if not cfg.get("digest", True):
            return
        ups = self.bus.utterances(self.run_id, rnd, seq=0)
        if not ups:
            return
        parts = []
        for u in ups:
            sentence = u["body"].split(". ")[0].strip()
            parts.append(f"{u['sender']}: {sentence[:80]}")
        digest = {"round": rnd, "text": " | ".join(parts)[:400]}
        hist = self.bus.get("run.digest", []) or []
        hist.append(digest)
        self.bus.set("run.digest", hist[-3:])

    # ---------------- convergence ----------------
    def convergence_metrics(self, rnd: int) -> dict:
        conv = self.rc.get("convergence", {})
        if not conv.get("enabled", True):
            return {}
        cur = self.bus.utterances(self.run_id, rnd, seq=0)
        prev = self.bus.utterances(self.run_id, rnd - 1, seq=0)
        if not cur:
            return {}
        model = conv.get("model") or None
        vecs, method = embeddings.embed([u["body"] for u in cur], model)
        within = embeddings.mean_pairwise(vecs)
        drift = None
        if prev:
            pvecs, _ = embeddings.embed([u["body"] for u in prev], model)
            n = min(len(vecs), len(pvecs))
            drift = sum(embeddings.cosine(vecs[i], pvecs[i]) for i in range(n)) / n
        m = {"round": rnd, "method": method, "within_round_mean": round(within, 4),
             "round_over_round": None if drift is None else round(drift, 4)}
        self.metrics.append(m)
        self.bus.event("convergence", json.dumps(m))
        return m

    def convergence_reason(self, rnd: int) -> str:
        """Adaptive stop: plateau, monotone rise, or a hard similarity ceiling.

        A fixed threshold does not transfer across topics/model sets; these three rules
        are read off the run's own series instead.
        """
        conv = self.rc.get("convergence", {})
        if not conv.get("enabled", True):
            return ""
        series = [m["round_over_round"] for m in self.metrics if m.get("round_over_round") is not None]
        if len(series) < 3:
            return ""
        mode = conv.get("mode", "adaptive")
        plateau = float(conv.get("plateau_delta", 0.02))
        hard = float(conv.get("hard_threshold", 0.85))
        min_rounds = int(conv.get("min_rounds", 5))
        rise = float(conv.get("rise_delta", 0.08))
        if rnd < min_rounds:
            return ""
        last = series[-1]
        if last >= hard:
            return f"converged (hard ceiling {last:.3f} >= {hard})"
        if mode == "adaptive":
            # 3 consecutive rises with a meaningful total gain
            if len(series) >= 3 and series[-3] < series[-2] < series[-1] \
                    and (series[-1] - series[-3]) >= rise:
                return (f"converged (3 rises {series[-3]:.3f}->{series[-1]:.3f} "
                        f"gain>={rise})")
            # plateau: last two deltas inside the noise band
            if len(series) >= 3 and abs(series[-1] - series[-2]) <= plateau \
                    and abs(series[-2] - series[-3]) <= plateau:
                return (f"plateau (deltas <= {plateau}: {series[-3]:.3f},"
                        f"{series[-2]:.3f},{series[-1]:.3f})")
        else:  # legacy fixed threshold
            thr = float(conv.get("threshold", 0.97))
            if last >= thr:
                return f"converged (cos {last:.3f} >= {thr})"
        return ""

    def should_stop(self, rnd: int) -> str:
        conv = self.rc.get("convergence", {})
        if self.bus.get("run.stop"):
            return "stop flag"
        budget = int(self.rc.get("token_budget", 40000) or 0)
        if budget > 0 and self.bus.tokens(self.run_id) > budget:
            return f"token budget ({budget})"
        if time.time() - self.started > float(self.rc.get("wall_timeout", 1800)):
            return "wall timeout"
        if conv.get("enabled", True):
            why = self.convergence_reason(rnd)
            if why:
                return why
        return ""

    # ---------------- run ----------------
    def run(self) -> dict:
        self.banner()
        self.started = time.time()
        self.start_sampler()
        for sp in self.speakers:
            self.spawn(sp)
        time.sleep(1.0)
        reason, rnd = "", 0
        try:
            for rnd in range(1, int(self.rc["rounds"]) + 1):
                if self.rc["mode"] == "serial":
                    self.serial_round(rnd)
                else:
                    self.panel_round(rnd)
                self.convergence_metrics(rnd)
                self.curate()
                self.update_digest(rnd)
                reason = self.should_stop(rnd)
                if reason:
                    log(f"stopping after round {rnd}: {reason}")
                    break
        finally:
            self.bus.set("run.stop", 1)
            time.sleep(2.0)
            for p in self.procs.values():
                if p.poll() is None:
                    p.terminate()
            for p in self.procs.values():
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.stop_sampler()

        summary = {
            "run_id": self.run_id, "config": self.rc, "rounds_done": rnd,
            "utterances": self.bus.count(self.run_id), "tokens": self.bus.tokens(self.run_id),
            "wall_s": round(time.time() - self.started, 1), "stopped": reason or "completed",
            "missing": {str(k): v for k, v in self.missing.items()},
            "restarts": self.restarts, "metrics": self.metrics,
            "board_entries": len(self.bus.board(self.run_id, limit=999)),
            "memory_entries": self.bus.conn.execute(
                "SELECT COUNT(*) FROM memory WHERE run_id=?", (self.run_id,)).fetchone()[0],
            "per_speaker": {},
        }
        for name in self.names:
            sp = next(s for s in self.speakers if s["name"] == name)
            row = self.bus.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(tokens),0), COALESCE(AVG(latency_ms),0) "
                "FROM messages WHERE run_id=? AND sender=? AND kind='utterance'",
                (self.run_id, name)).fetchone()
            summary["per_speaker"][name] = {"model": sp["model"], "utterances": row[0],
                                            "tokens": row[1], "avg_latency_ms": round(row[2])}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self.bus.event("run_end", summary["stopped"])
        log(f"done: {summary['utterances']} utterances, {summary['tokens']} tokens, "
            f"{summary['wall_s']}s, board={summary['board_entries']}, "
            f"mem={summary['memory_entries']} -> {self.dir}")
        return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    Supervisor(Path(args.config), args.run_id).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
