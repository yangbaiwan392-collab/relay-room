"""One speaker = one model + one persona + optional private memory + blackboard + mentions.

Reads the current round/phase from the bus, builds a view (persona, private notes, shared
blackboard, recent room, any direct mention), generates one utterance, and writes it back.
Idempotent by (run, round, seq=0) so restarts never duplicate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

from bus import Bus
from protocol import make

OLLAMA = "http://127.0.0.1:11434"
THINK = re.compile(r"<think>.*?</think>", re.S)
TAG_BOARD = re.compile(r"<board>(.*?)</board>", re.S | re.I)
TAG_NOTE = re.compile(r"<note>(.*?)</note>", re.S | re.I)
MENTION = re.compile(r"@([a-z0-9_]{2,20})\b", re.I)


def clean_reply(text: str) -> tuple[str, list[str], list[str]]:
    """Strip tags/markdown/prompt-echoes from a raw reply.

    Returns (clean_text, board_notes, private_notes). Extracted tags are removed from the
    visible utterance; prompt scaffolding the model echoed back is dropped entirely.
    """
    boards = TAG_BOARD.findall(text)
    notes = TAG_NOTE.findall(text)
    clean = TAG_BOARD.sub("", TAG_NOTE.sub("", text)).strip()
    clean = clean.replace("**", "")
    clean = re.sub(r"[『』「」\[\]]", "", clean)
    clean = re.sub(r"[（(]\s*注[:：][^）)]*[）)]", "", clean)
    clean = re.sub(r"[（(]\s*换一个角度或给新细节\s*[）)]", "", clean)
    clean = re.sub(r"[（(]\s*(约\s*)?\d+\s*字\s*[）)]", "", clean)
    clean = re.sub(r"(字数符合要求|符合字数要求|字数已达标|已控制在\d+字以内)", "", clean)
    clean = re.sub(r"^(已根据要求重写|已按要求重写|已重写|严格引用并保留关键信息).*$", "",
                   clean, flags=re.M)
    clean = re.sub(r"^-{3,}\s*$", "", clean, flags=re.M)
    # a model that copies a prompt sentence back out gets that sentence (and the rest) cut
    clean = re.sub(r"(你上一轮说过|不要再重复你之前说过的这些话|你刚才说的是|你之前说的是).*",
                   "", clean, flags=re.S)
    clean = clean.replace("'", "").replace('"', "")
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    clean = clean.strip("\"'“”‘’ ").strip()
    return clean, [b.strip()[:300] for b in boards], [n.strip()[:300] for n in notes]


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_model(model: str, system: str, user: str, *, num_ctx: int, max_tokens: int,
               temperature: float, keep_alive: str, timeout: int,
               repeat_penalty: float | None = None,
               frequency_penalty: float | None = None,
               think: bool = False) -> tuple[str, int, float]:
    options = {"num_predict": max_tokens, "temperature": temperature, "num_ctx": num_ctx}
    if repeat_penalty is not None:
        options["repeat_penalty"] = repeat_penalty
    if frequency_penalty is not None:
        options["frequency_penalty"] = frequency_penalty
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "think": bool(think),
        "options": options,
        "keep_alive": keep_alive,
    }
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        doc = json.loads(r.read().decode())
    msg = doc.get("message") or {}
    text = (msg.get("content") or "")
    if not text.strip():
        # thinking models can spend the whole budget reasoning; fall back to the trace
        text = (msg.get("thinking") or "")
    text = THINK.sub("", text).strip()
    text = re.sub(r"^\s*<think>.*", "", text, flags=re.S).strip()
    ev = int(doc.get("eval_count") or 0)
    ev_s = (doc.get("eval_duration") or 1) / 1e9
    return text, ev, round(ev / ev_s, 1) if ev_s else 0.0


def build_prompt(*, persona: str, topic: str, rnd: int, rounds: int, name: str,
                 transcript: list[dict], board: list[dict], memory: list[dict],
                 own: list[dict], mention: dict | None, digest: list[dict],
                 cfg: dict, opener: str | None = None,
                 bridge: dict | None = None) -> tuple[str, str]:
    system = persona + (
        "\n\nYou are in a live group chat on one GPU with the other speakers. Rules: "
        "at most two sentences; add ONE point nobody has said yet; never prefix your reply "
        "with your own name; do not merely agree."
    )
    if cfg.get("language") in ("zh", "chinese", "zh-CN"):
        system += ("\n\n必须始终用简体中文回答，不要用英文，不要夹杂英文单词。回复控制在两句话以内。")
    if cfg.get("style") == "human":
        banned = cfg.get("banned_words") or (
            "赋能、协同、闭环、抓手、生态、维度、链路、落地、底层逻辑、方法论、价值闭环、"
            "多维度、全链路、组合拳、范式、颗粒度、对齐、心智")
        system += (
            "\n\n【说话方式】像在饭桌上跟朋友聊天：短句、口语、具体。"
            f"禁止使用这些词：{banned}。不要排比，不要小标题，不要总结陈词，不要'首先其次最后'。"
            "每轮只说一个观点；可以不同意别人，可以说'我不知道'；最好带一个生活里的具体例子。"
            "不要解释你是怎么回答的，不要写'（注：……）'，不要复述提示里的任何句子。")
    extras = []
    mcfg = cfg.get("mentions", {})
    if mcfg.get("enabled"):
        if mcfg.get("mode") == "handoff":
            extras.append("To hand off work, start with `@name: <one concrete request>` "
                          "(e.g. `@phi: measure the VRAM ceiling of three 7B models`); that "
                          "speaker must answer with a concrete next action next round.")
        else:
            extras.append("To address one specific speaker, write @name (e.g. `@llama`) at the "
                          "start of a sentence; that speaker will answer you directly next round.")
    if cfg.get("board", {}).get("enabled"):
        extras.append("Post at most ONE `<board>fact</board>` per round, and only if it adds "
                      "a fact not already on the blackboard; never restate the topic there.")
    if cfg.get("memory", {}).get("enabled"):
        extras.append("You may keep at most one `<note>short fact</note>` per round; a note "
                      "must be a concrete fact or decision, not a restatement.")
    if extras:
        system += "\n" + " ".join(extras)

    parts = [f"Topic: {topic}", f"Round {rnd} of {rounds}."]
    relay = cfg.get("_relay")
    if relay and relay.get("body"):
        if cfg.get("relay_style") == "structured" and relay.get("claim"):
            parts.append(f"上一位 {relay.get('sender')} 的主张：「{relay.get('claim')}」")
            if relay.get("reason"):
                parts.append(f"他给的理由：「{relay.get('reason')}」")
            if relay.get("tail") and relay.get("tail") != relay.get("claim"):
                parts.append(f"他最后的落点：「{relay.get('tail')}」")
        else:
            parts.append(f"上一位 {relay.get('sender')} 刚说：「{str(relay.get('body'))[:200]}」")
        if cfg.get("relay_check") == "quote":
            parts.append("你必须先引用这句话里一个不少于 6 个字的原话短语（用引号），"
                         "说明你同意还是不同意，再补充你的新观点。不要换话题，不要无视他。")
        else:
            parts.append("先回应他的观点：说清你同意或不同意他的哪一点（不要整段复述他的原话），"
                         "再补充一条他没说过的新信息。不要换话题。")
    if bridge:
        parts.append(f"本轮是新话题的第一轮：话题从『{bridge.get('from') or '上一段'}』"
                     f"换到『{bridge.get('to') or topic}』。")
        parts.append("你负责过渡：先用一句话接住上一段聊到的**具体内容**（比如别人刚说的某个价钱或"
                     "某件事），再自然地把话带到新话题。不要生硬地说'我们换个话题'，也不要解释你在过渡。")
    note = cfg.get("_topic_note")
    if note:
        parts.append(note)
    angle = cfg.get("_angle")
    if angle:
        parts.append(f"本轮聚焦这个子问题：{angle}。不要重复前面已经提过的建议，给新的具体内容。")
    role = cfg.get("_role")
    if role:
        parts.append(f"本轮你的视角是：{role}。只从这个视角说话，不要重复你之前提过的具体建议或例子。")
    domain = cfg.get("_domain")
    if domain:
        parts.append(f"本轮领域是「{domain}」。不要用买菜、做饭、装修、工地这类日常类比来解释问题；"
                     f"举例子也必须来自「{domain}」这个领域。")
    locks = cfg.get("_locks") or {}
    if locks:
        parts.append("【本轮说话限制（必须遵守）】")
        if locks.get("banned_phrases"):
            parts.append("禁止出现这些句子或半句：" + "；".join(locks["banned_phrases"]))
        if locks.get("no_analogy"):
            parts.append("本轮禁止打比方：不要用'像 / 就像 / 好比 / 仿佛 / 如同'。")
        if locks.get("max_chars"):
            parts.append(f"整段不要超过 {locks['max_chars']} 个字。")
        parts.append("只用中文，不要出现英文单词。")
    cnd = cfg.get("_conductor")
    if cnd:
        parts.append("【云端主持人提示（本轮必须采纳，但不要说你收到了提示）】")
        if cnd.get("angle"):
            parts.append(f"本轮新角度：{cnd['angle']}")
        if cnd.get("fact"):
            parts.append(f"请引用这个具体数字或事实：{cnd['fact']}")
        if cnd.get("terms"):
            parts.append(f"必须用上这些领域概念中的至少一个：{cnd['terms']}")
        if cnd.get("challenge"):
            parts.append(f"挑战一下：{cnd['challenge']}")
        mine = (cnd.get("assignments") or {}).get(name)
        if mine:
            parts.append(f"【主持人给你的任务】{mine}")
        if cnd.get("banned"):
            parts.append(f"本轮不要提这些旧例子：{cnd['banned']}")
    if own:
        parts.append("你上一轮说过（不要重复这些句子，换一个角度或给新细节）：\n" +
                     "\n".join(f"- {o['body'][:120]}" for o in own[-2:]))
    if board:
        parts.append("SHARED BLACKBOARD (curated; do not repeat these):\n" +
                     "\n".join(f"- [{b['author']}] {b['text']}" for b in board))
    if digest:
        parts.append("COMPRESSED ROOM HISTORY (earlier rounds):\n" +
                     "\n".join(f"- r{d['round']}: {d['text']}" for d in digest))
    if memory or own:
        priv = []
        if memory:
            priv += [f"- [{m['kind']}] {m['text']}" for m in memory]
        if own:
            priv += [f"- (round {o['round']}) {o['body'][:160]}" for o in own]
        parts.append("YOUR PRIVATE MEMORY (only you see this):\n" + "\n".join(priv))
    room = "\n".join(f"[{t['sender']}] {t['body']}" for t in transcript) or "(the room is silent)"
    parts.append(f"The room so far:\n{room}")
    if opener:
        parts.append(f"本轮你的开场必须用这句：『{opener}』，然后再接你要说的观点。"
                     f"不要换别的开场，也不要解释这句开场。")
    if mention:
        if mcfg.get("mode") == "handoff":
            parts.append(f"{mention['sender'].upper()} HANDED YOU: \"{mention['text']}\"\n"
                         f"Reply with ONE concrete next action: say exactly what you will do, "
                         f"and include at least one number, unit, or artifact name "
                         f"(file/report/table/script/metric). No restatement.\n"
                         f"Good reply example: \"I'll run 20 warm-up requests, then record p95 "
                         f"latency in ms to results/latency.csv.\"")
        else:
            parts.append(f"{mention['sender'].upper()} ADDRESSED YOU DIRECTLY: "
                         f"\"{mention['text']}\"\n"
                         f"Answer them in ONE sentence that responds to their point, then stop.")
    else:
        parts.append(f"Your turn as {name.upper()}. Reply with your point only.")
    return system, "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--persona", required=True)
    ap.add_argument("--max-tokens", type=int, default=120)
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.85)
    ap.add_argument("--history", type=int, default=10)
    ap.add_argument("--keep-alive", default="30m")
    ap.add_argument("--model-timeout", type=int, default=300)
    args = ap.parse_args()

    bus = Bus(args.db)
    cfg = bus.get("run.cfg", {}) or {}
    try:
        persona = open(args.persona, encoding="utf-8").read() if args.persona.endswith(".txt") \
            else args.persona
    except OSError:
        persona = args.persona
    mem_cfg = cfg.get("memory", {})
    board_cfg = cfg.get("board", {})
    mention_cfg = cfg.get("mentions", {})

    bus.set(f"hb:{args.name}", time.time())
    bus.event("speaker_up", f"{args.name} ({args.model})")
    seen_round = -1
    fails = 0

    while True:
        if bus.get("run.stop"):
            bus.event("speaker_down", f"{args.name} stop flag")
            return 0
        rnd = int(bus.get("run.round") or 0)
        if rnd <= 0:
            time.sleep(0.4)
            continue
        mode = bus.get("run.mode", "panel")
        revise = bus.get("run.revise") or {}
        wanted = int(revise.get("seq", 1)) if revise else 0
        is_revision = (
            bool(revise)
            and int(revise.get("round", -1)) == rnd
            and revise.get("speaker") == args.name
            and wanted >= 1
        )
        allowed = ((is_revision and not bus.has(args.run_id, rnd, args.name, seq=wanted))
                   or not bus.has(args.run_id, rnd, args.name))
        if allowed and mode in ("serial", "relay") and not is_revision:
            allowed = bus.get("run.turn") == args.name
        seq = wanted if is_revision else 0
        if not allowed:
            if rnd != seen_round:
                bus.set(f"status:{args.name}", "waiting")
                seen_round = rnd
            bus.set(f"hb:{args.name}", time.time())
            time.sleep(0.4)
            continue

        if bus.tokens(args.run_id) >= int(bus.get("run.token_budget") or 10**9):
            time.sleep(0.5)
            continue

        if is_revision:
            bus.set(f"status:{args.name}", "revising")
            if revise.get("reason") == "dedup":
                system = (persona + "\n\n你正在重写自己刚才那句话，因为它和你之前说过的几乎一样。")
                user = (f"你之前说过：\n\"{revise.get('previous', '')}\"\n\n"
                        f"判定：{revise.get('check', '重复')}。\n"
                        f"请完全换一个角度：给一个新的具体例子、一个不同意的点，或者一个新数字。"
                        f"必须换掉原来的句式，用大白话，一句话，不要复述。")
            elif revise.get("reason") == "name":
                roster = "、".join((bus.get("run.names", []) or []))
                system = persona + "\n\n你正在重写自己刚才那句话，因为它提到了房间里并不存在的人。"
                user = (f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n"
                        f"里面出现了名单外的名字：{revise.get('text', '')}。\n"
                        f"房间里只有这些人：{roster}。请重写这句话，只使用名单里的名字，"
                        f"或者干脆不点名，不要解释你在改什么。")
            elif revise.get("reason") == "relay":
                system = persona + "\n\n你在重写自己刚才那句：你没有接住上一位的话。"
                if cfg.get("relay_check") == "quote":
                    rule = ("请重写：先用引号引用上一位原话里至少 6 个字的短语，说明你同意或不同意，"
                            "再补充你的新观点。保留你已经引用的部分，只修正没接住的地方。不要换话题。")
                else:
                    rule = ("请重写：先回应上一位的观点（同意或不同意他的哪一点），"
                            "再补充一条他没说过的新信息。不要换话题。")
                user = (f"上一位 {revise.get('sender', '')} 说的是：\n\"{revise.get('text', '')}\"\n\n"
                        f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n" + rule)
            elif revise.get("reason") == "phrase":
                system = persona + "\n\n你在重写自己刚才那句话：它踩了你说话习惯的禁区。"
                user = (f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n"
                        f"问题：{revise.get('text', '')}\n\n"
                        f"请完全换一种说法：换句式、不打比方、不夹英文、不要重复那些半句，"
                        f"并控制在要求字数内。只改有问题的部分，其余观点和引用保留。"
                        f"不要解释你在改什么。")
            elif revise.get("reason") == "domain":
                system = persona + "\n\n你在重写自己刚才那句话：它跑回了日常买菜/做饭/工地类比。"
                user = (f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n"
                        f"其中出现了不该用的日常词：{revise.get('text', '')}。\n"
                        f"请只谈当前领域的内容，例子也必须来自这个领域，不要用买菜/做饭/装修/工地类比，"
                        f"也不要解释你在改什么。")
            elif revise.get("reason") == "bridge":
                system = persona + "\n\n你在重写新话题的过渡句：刚才那句没有接住上一段。"
                user = (f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n"
                        f"上一段最后几句是：\n\"{revise.get('text', '')}\"\n\n"
                        f"请重写：先用一句话引用或回应上一段里的某个具体内容（价格、某件事、某个说法），"
                        f"再自然带到新话题『{bus.get('run.topic', '')}』。不要生硬宣告换话题。")
            elif revise.get("reason") == "topic":
                system = persona + "\n\n你正在重写自己刚才那句话，因为它提到了已经结束的旧话题。"
                user = (f"你刚才说的是：\n\"{revise.get('previous', '')}\"\n\n"
                        f"里面出现了旧话题的词：{revise.get('text', '')}。\n"
                        f"当前话题是：{bus.get('run.topic', '')}\n"
                        f"请只围绕当前这个话题重写一句话，不要再出现那些旧话题的词，也不要解释你在改什么。")
            else:
                system = (persona + "\n\nYou are rewriting one of your own replies because it "
                          "failed the group's handoff contract.")
                user = (f"Your previous reply was:\n\"{revise.get('previous', '')}\"\n\n"
                        f"{revise.get('sender', 'Someone')} asked: \"{revise.get('text', '')}\"\n\n"
                        f"It was rejected: it lacks a concrete action with a number, unit, or "
                        f"artifact name. Rewrite it as ONE sentence that states exactly what you "
                        f"will do and includes at least one number, unit (ms/%/GB/tokens) or "
                        f"artifact (file/report/table/script). No restatement.")
        else:
            bus.set(f"status:{args.name}", "speaking")
            topic_start = bus.get("run.topic_start")
            try:
                topic_start = int(topic_start) if topic_start else None
            except (TypeError, ValueError):
                topic_start = None
            transcript = bus.transcript(args.run_id, limit=args.history, upto_round=rnd,
                                        start_round=topic_start)
            bridge = bus.get("run.bridge") or {}
            bridge_here = (bridge.get("round") == rnd and bridge.get("speaker") == args.name)
            if bridge_here:
                # this turn opens a new topic: it must see the END of the previous one
                transcript = bus.transcript(args.run_id, limit=4, upto_round=rnd)
            board = bus.board(args.run_id, limit=int(board_cfg.get("max", 8))) \
                if board_cfg.get("enabled") else []
            memory = bus.memory_of(args.run_id, args.name,
                                   limit=int(mem_cfg.get("max_notes", 6))) \
                if mem_cfg.get("enabled") else []
            own_tail = int(mem_cfg.get("own_tail", cfg.get("own_tail", 2)))
            own = [o for o in bus.utterances(args.run_id, seq=0)
                   if o["sender"] == args.name][-own_tail:]
            mention = None
            if mention_cfg.get("enabled"):
                mmap = bus.get("run.mention_map", {}) or {}
                m = mmap.get(args.name)
                if m and int(m.get("round", 0)) == rnd - 1:
                    mention = m
            opener = None
            multi = bus.get("run.openers") or {}
            if multi and int(multi.get("round", -1)) == rnd:
                opener = (multi.get("map") or {}).get(args.name)
            if opener is None:
                o = bus.get("run.opener") or {}
                if o and int(o.get("round", -1)) == rnd and o.get("speaker") == args.name:
                    opener = o.get("text")
            cfg_local = dict(cfg)
            cfg_local["_topic_note"] = bus.get("run.topic_note", "") or ""
            ang = bus.get("run.angle") or {}
            cfg_local["_angle"] = ang.get("text") if ang.get("round") == rnd else None
            ro = bus.get("run.roles") or {}
            cfg_local["_role"] = (ro.get("map") or {}).get(args.name) \
                if ro.get("round") == rnd else None
            cn = bus.get("run.conductor") or {}
            cfg_local["_conductor"] = (cn.get("hint") or {}) if cn.get("round") == rnd else None
            cfg_local["_domain"] = bus.get("run.domain", "") or None
            cfg_local["_locks"] = (bus.get("run.locks") or {}).get(args.name)
            relay = bus.get("run.relay_prev") or {}
            if relay.get("body") and relay.get("sender") != args.name:
                cfg_local["_relay"] = relay
            system, user = build_prompt(
                persona=persona, topic=bus.get("run.topic", ""), rnd=rnd,
                rounds=int(bus.get("run.rounds") or 1), name=args.name,
                transcript=transcript, board=board, memory=memory, own=own,
                mention=mention, digest=bus.get("run.digest", []) or [], cfg=cfg_local,
                opener=opener, bridge=(bridge if bridge_here else None))

        t0 = time.time()
        err = ""
        tokens = 0
        tok_s = 0.0
        clean, boards, notes = "", [], []
        for attempt in range(2):
            try:
                text, tok, tps = call_model(
                    args.model, system, user, num_ctx=args.num_ctx, max_tokens=args.max_tokens,
                    temperature=args.temperature, keep_alive=args.keep_alive,
                    timeout=args.model_timeout,
                    repeat_penalty=cfg.get("repeat_penalty"),
                    frequency_penalty=cfg.get("frequency_penalty"),
                    think=bool(cfg.get("think", False)))
                err = ""
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                text, tok, tps = f"(model call failed: {type(exc).__name__})", 0, 0.0
                err = f"{type(exc).__name__}: {exc}"
                fails += 1
            tokens += tok
            tok_s = tps or tok_s
            clean, boards, notes = clean_reply(text)
            if clean:
                break
            # empty reply: one retry with an explicit nudge
            user = user + "\n\n（上一次回复是空的：这次必须直接给出正文。）"
        latency = int((time.time() - t0) * 1000)
        if not clean:
            first = (boards[0] if boards else (notes[0] if notes else "")).strip()
            clean = f"(posted: {first[:100]})" if first else "(empty reply)"
        # a model sometimes starts its line with its own name as a speaker label
        for pre in (f"{args.name}：", f"{args.name}:", f"{args.name}说：", f"{args.name}说:"):
            if clean.startswith(pre):
                clean = clean[len(pre):].lstrip()
                break
        # hard cap for relay turns: cut at a sentence boundary instead of rambling
        locks = cfg_local.get("_locks") or {}
        cap = int(locks.get("max_chars", 0) or 0)
        if cap and len(clean) > int(cap * 1.2):
            cut = clean[:int(cap * 1.2)]
            for sep in ("。", "！", "？", "；", ".", "!", "?"):
                pos = cut.rfind(sep)
                if pos >= int(cap * 0.6):
                    cut = cut[:pos + 1]
                    break
            clean = cut.rstrip() + "…"
        for b in boards:
            bus.add_board(args.run_id, rnd, args.name, b)
        for n in notes:
            bus.add_memory(args.run_id, args.name, rnd, "note", n.strip()[:300])

        row = make(run_id=args.run_id, rnd=rnd, sender=args.name, kind="utterance",
                   seq=seq, body=clean, tokens=tokens, latency_ms=latency,
                   meta={"model": args.model, "tok_s": tok_s, "error": err,
                         "boards": len(boards), "notes": len(notes),
                         "revision": is_revision,
                         "mentions": MENTION.findall(clean)})
        inserted = bus.send(row)
        if inserted and is_revision and seq >= 1:
            # replace-style revision: older versions of this turn disappear from every view
            bus.supersede(args.run_id, rnd, args.name, keep_seq=seq)
        bus.set(f"status:{args.name}", "idle")
        bus.set(f"hb:{args.name}", time.time())
        if inserted:
            tag = "revision" if is_revision else "utterance"
            bus.event(tag, f"{args.name} r{rnd} {tokens}tok {latency}ms")
        if fails >= 3:
            bus.event("speaker_degraded", f"{args.name}: {err}")
            fails = 0
        seen_round = rnd


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
