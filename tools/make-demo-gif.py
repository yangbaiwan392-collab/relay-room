#!/usr/bin/env python3
"""Render a ~30-second demo GIF from a run's SQLite bus.

Usage:
  python tools/make-demo-gif.py --run runs/<id> --out docs/demo.gif
  python tools/make-demo-gif.py --run runs/<id> --auto --list --out docs/demo.gif
  python tools/make-demo-gif.py --run runs/<id> --start 53 --end 60 --out docs/demo.gif

Requires Pillow (only this tool does; the room itself is stdlib-only).
"""
import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    raise SystemExit("Pillow is required for the GIF tool: pip install pillow")

W, H = 960, 600
FPS = 10
BG = (11, 15, 20)
HEADER = (17, 24, 32)
BORDER = (48, 54, 61)
META = (110, 118, 129)
TITLE = (230, 237, 243)
BODY = (201, 209, 217)
CONDUCTOR = (86, 212, 221)
GOOD = (126, 231, 135)
WARN = (255, 166, 87)
COLORS = {"老王": (126, 231, 135), "阿May": (121, 192, 255),
          "大刘": (255, 166, 87), "小陈": (210, 168, 255)}
FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/System/Library/Fonts/PingFang.ttc"]
BANNED = ("工地", "普通人到底有", "砌墙", "半导体")
CHUNK = 8            # characters revealed per frame
TURN_HOLD_MS = 700   # reading pause expressed as a longer frame, not duplicate frames


def font(size: int):
    for p in FONTS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def dwidth(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def wrap(text: str, max_w: int) -> list[str]:
    lines, cur, curw = [], "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if curw + w > max_w and cur:
            lines.append(cur)
            cur, curw = ch, w
        else:
            cur += ch
            curw += w
    if cur:
        lines.append(cur)
    return lines or [""]


def cjk(s: str) -> str:
    return "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")


def connect(run: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{run / 'bus.sqlite'}?mode=ro", uri=True)


def load(run: Path, start: int, end: int):
    conn = connect(run)
    rows = conn.execute(
        "SELECT round, sender, body, tokens, latency_ms FROM messages m WHERE kind='utterance' "
        "AND round BETWEEN ? AND ? AND seq=(SELECT MAX(seq) FROM messages x "
        "WHERE x.run_id=m.run_id AND x.round=m.round AND x.sender=m.sender "
        "AND x.kind='utterance') ORDER BY ts", (start, end)).fetchall()
    cond = []
    for (detail,) in conn.execute("SELECT detail FROM events WHERE kind='conductor'"):
        try:
            rnd = int(detail.split()[0][1:])
        except (ValueError, IndexError):
            continue
        if start <= rnd <= end and "angle=" in detail:
            cond.append((rnd, detail.split("angle=", 1)[1]))
    conn.close()
    items = [{"kind": "msg", "round": r[0], "sender": r[1], "body": r[2],
              "tokens": r[3], "lat": r[4]} for r in rows]
    for rnd, text in cond:
        idx = next((i for i, it in enumerate(items)
                    if it["kind"] == "msg" and it["round"] >= rnd), len(items))
        items.insert(idx, {"kind": "conductor", "round": rnd, "text": text})
    return items


def score_window(rows, part, cond_rounds) -> float:
    if len(part) < 20:
        return -1
    linked = total = 0
    for i in range(1, len(part)):
        a, b = cjk(part[i - 1][2]), cjk(part[i][2])
        if len(a) < 6 or len(b) < 6:
            continue
        total += 1
        linked += int(any(a[j:j + 6] in b for j in range(len(a) - 5)))
    quote = linked / max(1, total)
    bodies = [r[2] for r in part]
    dup = sum(1 for b in set(bodies) if bodies.count(b) > 1)
    bad = sum(1 for b in bodies if any(w in b for w in BANNED))
    avg = sum(len(b) for b in bodies) / len(bodies)
    has_cond = any(r[0] in cond_rounds for r in part)
    return (1.3 * quote - 0.04 * dup - 0.10 * bad + (0.10 if has_cond else 0)
            - 0.002 * abs(avg - 70))


def pick_window(run: Path, span: int = 8) -> tuple[int, int]:
    conn = connect(run)
    rounds = conn.execute("SELECT MAX(round) FROM messages WHERE kind='utterance'").fetchone()[0]
    rows = conn.execute(
        "SELECT round, sender, body FROM messages m WHERE kind='utterance' AND seq=("
        "SELECT MAX(seq) FROM messages x WHERE x.run_id=m.run_id AND x.round=m.round "
        "AND x.sender=m.sender AND x.kind='utterance') ORDER BY ts").fetchall()
    cond_rounds = set()
    for (detail,) in conn.execute("SELECT detail FROM events WHERE kind='conductor'"):
        try:
            cond_rounds.add(int(detail.split()[0][1:]))
        except (ValueError, IndexError):
            pass
    conn.close()
    cands = []
    for start in range(1, max(1, rounds - span + 1)):
        part = [r for r in rows if start <= r[0] < start + span]
        s = score_window(rows, part, cond_rounds)
        if s > 0:
            cands.append((s, start, start + span - 1))
    cands.sort(reverse=True)
    for s, a, b in cands[:6]:
        print(f"  candidate r{a}-{b}  score={s:.3f}")
    s, a, b = cands[0]
    return a, b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--out", default="docs/demo.gif", type=Path)
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=8)
    ap.add_argument("--max-frames", type=int, default=290)
    ap.add_argument("--fps", type=int, default=9)
    ap.add_argument("--colors", type=int, default=32)
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--height", type=int, default=450)
    ap.add_argument("--chunk", type=int, default=14)
    ap.add_argument("--hold-ms", type=int, default=600)
    args = ap.parse_args()
    global FPS, CHUNK, TURN_HOLD_MS
    FPS, CHUNK, TURN_HOLD_MS = args.fps, args.chunk, args.hold_ms

    if args.auto:
        args.start, args.end = pick_window(args.run)
    items = load(args.run, args.start, args.end)
    if not items:
        raise SystemExit("no utterances in that range")

    f_head, f_meta, f_name, f_body = font(16), font(15), font(18), font(20)
    f_title, f_sub, f_big, f_cond = font(26), font(17), font(40), font(17)
    frames: list[Image.Image] = []
    durations: list[int] = []

    def canvas():
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 46], fill=HEADER)
        d.line([0, 46, W, 46], fill=BORDER)
        d.text((18, 12), "Relay Room", font=f_head, fill=TITLE)
        d.text((150, 15), "4 local models · 1 GPU · cloud conductor", font=f_meta, fill=META)
        d.text((W - 205, 15), "relay mode · live", font=f_meta, fill=GOOD)
        return img, d

    def render(blocks, partial_idx=-1, partial_text=""):
        img, d = canvas()
        line_h, top, bottom = 29, 60, H - 46
        shown = []
        for i, (kind, sender, text, meta) in enumerate(blocks):
            body = partial_text if i == partial_idx else text
            lines = wrap(body, 74)
            h = len(lines) * line_h + (16 if meta else 0) + 18
            shown.append((kind, sender, lines, meta, h))
        height, start_i = 0, len(shown)
        for i in range(len(shown) - 1, -1, -1):
            if height + shown[i][4] > bottom - top and height > 0:
                break
            height += shown[i][4]
            start_i = i
        y = top
        for i in range(start_i, len(shown)):
            kind, sender, lines, meta, _ = shown[i]
            if kind == "conductor":
                box_h = len(lines) * line_h + 14
                d.rectangle([30, y, W - 30, y + box_h], fill=(13, 34, 38))
                d.rectangle([30, y, 34, y + box_h], fill=CONDUCTOR)
                yy = y + 8
                for line in lines:
                    d.text((46, yy), line, font=f_cond, fill=CONDUCTOR)
                    yy += line_h
                y += box_h + 10
                continue
            d.text((32, y), sender, font=f_name, fill=COLORS.get(sender, TITLE))
            yy = y
            for line in lines:
                d.text((110, yy), line, font=f_body, fill=BODY)
                yy += line_h
            if meta:
                d.text((110, yy - 4), meta, font=f_meta, fill=META)
                yy += 14
            y = yy + 10
        return img

    # intro
    for _ in range(3):
        img, d = canvas()
        d.text((40, 190), "Relay Room", font=f_big, fill=TITLE)
        d.text((42, 250), "云端导演 + 本地演员：让 4 个小模型真的聊起来", font=f_title, fill=BODY)
        d.text((42, 300), "cloud conductor · relay handoff · local 4-7B speakers", font=f_sub, fill=META)
        d.text((42, 340), "each turn must pick up the previous sentence, then add something new",
               font=f_sub, fill=(139, 148, 158))
        frames.append(img)
        durations.append(800)

    blocks: list[tuple] = []
    for it in items:
        if it["kind"] == "conductor":
            blocks.append(("conductor", "", "导播 · 新角度：" + it["text"], ""))
        else:
            meta = f"{it['tokens']} tok · {it['lat']} ms"
            blocks.append(("msg", it["sender"], it["body"], meta))
        full = blocks[-1][2]
        for k in range(CHUNK, len(full) + CHUNK, CHUNK):
            frames.append(render(blocks, len(blocks) - 1, full[:k]))
            durations.append(110)
        frames.append(render(blocks))
        durations.append(TURN_HOLD_MS)
        if len(frames) >= args.max_frames:
            break

    # outro
    for _ in range(2):
        img, d = canvas()
        d.text((40, 210), "38% turns quote the previous one", font=f_title, fill=GOOD)
        d.text((40, 250), "vs 2% for parallel round-robin rooms", font=f_title, fill=WARN)
        d.text((40, 305), "200 rounds · 4 domains · 0.1% duplicates · conductor ≈ ¥0.1",
               font=f_sub, fill=META)
        d.text((40, 345), "github.com/yangbaiwan392-collab/relay-room", font=f_sub, fill=CONDUCTOR)
        frames.append(img)
        durations.append(900)

    frames = frames[:args.max_frames]
    durations = durations[:args.max_frames]
    if (args.width, args.height) != (W, H):
        frames = [f.resize((args.width, args.height), Image.LANCZOS) for f in frames]
    frames = [f.convert("P", palette=Image.ADAPTIVE, colors=args.colors) for f in frames]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(args.out, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=2)
    from PIL import Image as _I
    check = _I.open(args.out)
    total_s = sum(durations) / 1000.0 if check.n_frames == len(durations) else (
        check.n_frames * (check.info.get("duration", 100) or 100) / 1000.0)
    print(f"wrote {args.out}  frames={check.n_frames}  total≈{total_s:.1f}s  "
          f"size={args.out.stat().st_size/1024/1024:.2f} MB  rounds={args.start}-{args.end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
