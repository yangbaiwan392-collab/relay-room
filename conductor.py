#!/usr/bin/env python3
"""Cloud conductor: every N rounds, give the local room a fresh angle + a concrete fact.

Local 4-7B speakers exhaust a topic in 10-15 rounds and start recycling. The conductor
reads the recent transcript and supplies what small models cannot invent on demand:
a new sub-angle, a concrete number/fact to cite, and one challenge to aim at a speaker.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENDPOINT = os.environ.get("CONDUCTOR_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("CONDUCTOR_MODEL", "deepseek-flash")
LEGACY_KEY_FILE = Path.home() / ".workbuddy" / "models.json"
USAGE_LOG = HERE / "runs" / "conductor-usage.jsonl"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SYSTEM = """你是一个小型多模型聊天室的“云端主持人”。房间里是 4 个本地小模型（4B-7B），
它们会在同一个话题聊 10-15 轮后开始复读、空转、互相附和。

你的任务：给它们下一次发言注入它们自己产不出的东西。
- 如果有“当前领域”，必须把讨论牢牢按在该领域内，不要退回日常购物/省钱话题。
- 如果要求“开新话题”，先给出一个能立住的具体话题，再给角度、事实、挑战。
- 事实要短、可引用；不确定精确性时给合理区间，不要编造可核查的假数据。

只输出 JSON：{"topic": "...", "angle": "...", "fact": "...", "terms": "三个该领域专用概念（逗号分隔）",
"assignments": {"成员名": "本轮该成员的具体任务或立场（一句话）"}, "banned": "本轮禁止再提的旧例子关键词，逗号分隔", "challenge": "..."}
（不需要 topic 时该字段可以是空字符串）。所有字段简体中文，每项不超过 45 字。"""


def key() -> str:
    """API key from the environment; never stored in the repo."""
    for var in ("CONDUCTOR_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(var)
        if value:
            return value
    # optional local convenience: {"apiKey": ...} entries in a models.json
    if LEGACY_KEY_FILE.exists():
        for entry in json.loads(LEGACY_KEY_FILE.read_text(encoding="utf-8")):
            if "deepseek" in json.dumps(entry).lower() and entry.get("apiKey"):
                return entry["apiKey"]
    raise SystemExit(
        "No conductor API key. Set CONDUCTOR_API_KEY (optionally CONDUCTOR_URL / "
        "CONDUCTOR_MODEL for any OpenAI-compatible endpoint)."
    )


def chat(messages: list[dict], temperature: float = 0.6, max_tokens: int = 1500) -> tuple[str, dict]:
    payload = {"model": MODEL, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key()}"})
    with _OPENER.open(req, timeout=45) as r:
        doc = json.loads(r.read().decode())
    msg = doc["choices"][0]["message"]
    text = msg.get("content") or msg.get("reasoning_content") or ""
    return text, doc.get("usage") or {}


def parse_hint(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    doc: dict = {}
    if m:
        try:
            doc = json.loads(m.group(0))
        except json.JSONDecodeError:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    out = {k: str(doc.get(k, "")).strip() for k in ("topic", "angle", "fact", "terms",
                                                     "banned", "challenge")}
    if not out["angle"]:
        # per-field regex fallback when the JSON is truncated
        for k in ("angle", "fact", "terms", "banned", "challenge"):
            mm = re.search(rf'"{k}"\s*:\s*"([^"]{2,80})"', text)
            if mm:
                out[k] = mm.group(1).strip()
    assign = doc.get("assignments")
    if isinstance(assign, dict):
        out["assignments"] = {str(k): str(v).strip() for k, v in assign.items() if str(v).strip()}
    elif isinstance(assign, str):
        pairs = {}
        for part in re.split(r"[;；\n]", assign):
            sep = ":" if ":" in part else ("：" if "：" in part else "")
            if not sep:
                continue
            k, v = part.split(sep, 1)
            if k.strip() and v.strip():
                pairs[k.strip()] = v.strip()
        if pairs:
            out["assignments"] = pairs
    return {k: v for k, v in out.items() if v}


def hint(*, topic: str, rnd: int, recent: list[dict], used_angles: list[str],
         dedup_rate: float, roster: list[str], domain: str = "",
         domain_hint: str = "", new_topic: bool = False) -> dict:
    lines = [f"{u['sender']}：{u['body'][:120]}" for u in recent[-8:]]
    user = json.dumps({
        "当前话题": topic,
        "当前领域": domain,
        "领域提示": domain_hint,
        "是否开新话题": new_topic,
        "当前轮次": rnd,
        "房间成员": roster,
        "最近发言": lines,
        "已经用过的角度": used_angles[-12:],
        "最近去重率（每100轮）": round(dedup_rate, 1),
    }, ensure_ascii=False)
    t0 = time.time()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user}]
    hint_doc: dict = {}
    usage: dict = {}
    for attempt in range(2):
        try:
            text, usage = chat(messages)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            if attempt == 1:
                return {"error": f"{type(exc).__name__}: {exc}"}
            continue
        hint_doc = parse_hint(text)
        if hint_doc:
            break
        messages = messages[:2] + [{"role": "user", "content": "只输出 JSON，不要任何解释或前后缀。"}]
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "run_round": rnd,
                                "usage": usage, "latency_s": round(time.time() - t0, 2),
                                "hint": hint_doc}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return hint_doc or {"error": "unparsable"}


if __name__ == "__main__":
    print(json.dumps(hint(topic="测试", rnd=1, recent=[], used_angles=[],
                          dedup_rate=0, roster=["a", "b"]), ensure_ascii=False, indent=2))
