# PROMO — 发布文案与渠道（可直接复制）

> 一个没人知道的开源仓库 = 没人看的仓库。下面是可以直接用的文案与渠道顺序。
> 原则：**先给结论和数字，再给链接；承认限制；回复评论比发帖更重要。**

## 发布前 5 分钟检查

- [ ] README 首屏有 GIF + 一句结论（38% vs 2%）
- [ ] 仓库 topics：`llm, multi-agent, ollama, local-llm, multi-model, chatroom, relay, orchestration, python`
- [ ] About 里有 description；勾选 Releases/Wiki 不需要
- [ ] 确认 README 里的命令能跑（`configs/starter.json` 冒烟 10 轮）
- [ ] 截图/GIF 体积 < 10 MB（当前 ~4–6 MB）
- [ ] 没有密钥、没有 runs/、没有个人路径

## 渠道与顺序（技术类）

1. **r/LocalLLaMA**（英文，最相关，人数最多）
2. **Hacker News — Show HN**（英文，挑美西时间早上发）
3. **X / Twitter**（配 GIF，3 条串）
4. **V2EX 分享创造**（中文）
5. **即刻 / 知乎 / 小红书**（中文，选一个即可）
6. 相关 GitHub topic 下持续维护 issue（长期）

## 英文文案

### r/LocalLLaMA

**Title:** I built a relay chat room where 4 local models actually take turns quoting each other (38% vs 2% for round-robin)

**Body:**
> I kept putting 4 small local models (7B + 4B + 4B + 2B) in one room and getting 4 monologues — they answered the topic, never each other.
>
> So I changed two things:
> 1. **Relay**: the supervisor hands the next speaker the last ~100 chars of the previous sentence and requires a short quote/response first (guard rewrites otherwise).
> 2. **Cloud conductor**: a cheap cloud model reads the transcript tail every 8 rounds and injects a fresh angle, one concrete fact, per-speaker assignments and a dynamic ban list. Local 4B models can't invent new substance on demand.
>
> Measured on the same topic/models, 50–200 rounds: turns quoting the previous one went **2% → 38%**; verbatim duplicates **0.5% → 0.1%**; 200 rounds across 4 domains without collapse.
>
> Cost: speakers are local; conductor is ~24 calls / 30k tokens per 200 rounds (≈ $0.02).
>
> Honest limits: ~80% of turns get rewritten under strict-quote mode (small models fail the first quote); replacing the previous sentence with a structured summary drops continuity to 8%; no human eval yet.
>
> Code + measurements + pitfalls: https://github.com/yangbaiwan392-collab/relay-room
> Would love feedback on the relay/guard design or better turn-taking policies.

### Hacker News — Show HN

**Title:** Show HN: Relay Room – a cloud conductor makes 4 local LLMs hold one conversation

**Body:** same as above, trimmed to ~120 words, add: "No pip deps; Python stdlib + Ollama. Runs on one 16 GB GPU."

### X / Twitter（3 条）

1. 4 local LLMs in one room → 4 monologues. Only 2% of turns referenced the previous speaker.
2. Fix: a cheap cloud "conductor" injects angles/facts/assignments every N rounds, and each local speaker must quote the previous sentence before adding a point. Result: 2% → 38% continuity, 0.1% duplicates.
3. Open source, one 16 GB GPU, conductor ≈ $0.02 per 200 rounds. Code + measurements: https://github.com/yangbaiwan392-collab/relay-room

## 中文文案

### V2EX 分享创造

**标题：** 让 4 个本地小模型真的接话：云端导演 + 接力机制（附 200 轮实测）

**正文：**
> 之前把几个 4B/7B 放在一个"房间"里，它们各说各的，15 轮后开始复读。
>
> 改了两点：
> 1. 接力：主管把上一位最后 100 字递给下一位，要求先引用/回应再补新观点（不合格退回重写）；
> 2. 云端导演：每 8 轮让云端模型读最近的对话，给新角度、一个具体事实、分人任务和动态禁词。本地 4B 自己产不出新东西，云端可以。
>
> 同一批模型、同一话题实测：引用上一位的比例 **2% → 38%**，逐字重复 **0.5% → 0.1%**，200 轮跨四个领域没塌。
>
> 成本：speaker 全本地；主持人 200 轮约 24 次调用、3 万 tokens，几分钱。
>
> 坑也写进仓库了（20 条），包括"结构化摘要会把接续率打到 8%"这种负结果。
> 代码与数据：https://github.com/yangbaiwan392-collab/relay-room

### 即刻 / 小红书（短）

> 让一台 16G 显卡上的 4 个小模型真的聊起来：
> 云端只花几分钱当"导播"，本地模型接力接话。
> 接续率从 2% 提到 38%，逐字重复降到 0.1%。
> 开源：github.com/yangbaiwan392-collab/relay-room

## 发布节奏

- **Day 0**：r/LocalLLaMA（美西周二/周三 8–10am）+ X 串
- **Day 1**：Show HN（同一周内，别和 Reddit 同日）
- **Day 2**：V2EX / 即刻
- 之后：把评论里最高频的问题补进 README / FAQ，再回帖更新（"updated with X"）
- **回复所有技术评论**：前 2 小时的互动决定帖子能不能被推

## 心态

新仓库默认没人看，这很正常——不是你的项目差，是**没有任何分发**。一个 500 字的帖子 + 一张会动的 GIF，
比再改 3 天代码更能决定有没有人知道它。
