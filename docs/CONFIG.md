# CONFIG — 配置字段参考

配置文件是 JSON，由 `supervisor.py --config` 读取。两级结构：`run`（房间级）+ `speakers`（角色）。

## run

| 字段 | 默认 | 说明 |
|---|---|---|
| `name` | — | 显示名 |
| `topic` / `seed_topic` | — | 初始话题（用 `domains` 时由主持人生成话题） |
| `mode` | `panel` | **`relay`（推荐）**、`panel`（平行独白）、`serial`（串行但无接力校验） |
| `rounds` | 8 | 总轮数 |
| `domain_rounds` | — | 每个领域的轮数；与 `domains` 配合循环 |
| `domains` | — | `[{name, rounds, hint, avoid}]`；`avoid` 会被 domain_guard 用作禁词 |
| `conductor` | `{enabled:false}` | `{enabled, every, model}`；`every` 建议 4–8 |
| `relay_check` | `quote` | `quote`：必须引用 ≥6 字原话；`respond`：关键词回应即可 |
| `relay_style` | `plain` | `plain`：递原话；`structured`：递"主张/理由/落点"（实测更差，见 RESULTS） |
| `max_tokens` | 120 | 单条发言 token 上限（中文约 1 字 ≈ 1–2 token） |
| `num_ctx` | 4096 | 每个 speaker 的上下文窗口 |
| `history` | 10 | 传给 speaker 的历史条数 |
| `token_budget` | 40000 | 整场 token 预算；**0 = 不限**。设小了会中途停（见 PITFALLS） |
| `wall_timeout` | — | 整场墙钟上限（秒） |
| `turn_timeout` | 150 | 单轮等待上限（秒） |
| `repair_timeout` | 90 | 单次重写等待上限（秒） |
| `language` | — | `zh` 时追加"必须用简体中文" |
| `style` | — | `human` 时启用"说人话"规则 + 禁套话词表 |
| `think` | false | 思考型模型必须 false，否则正文为空 |
| `repeat_penalty` / `frequency_penalty` | — | 传给 Ollama 的采样参数 |
| `sample_vram` | true | 是否采样显存（需要 nvidia-smi） |

### dedup

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | false | 是否开启"和过去发言太像就重写" |
| `threshold` | 0.9 | embedding 余弦阈值 |
| `skeleton_threshold` | 0.85 | 骨架（中文掩码 n-gram）阈值，抓"换名词不换句式"的模板 |
| `own_window` | 4 | 回看自己最近多少条 |
| `max_per_round` | 2 | 每轮最多重写几条 |
| `per_speaker` | — | 分角色的阈值覆盖，例如 `{"小陈":0.84}` |

### phrase_guard（说话习惯锁）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | false | 开启后按"禁句 / 重复片段 / 英文词 / 超长"重写 |
| `ngram_n` | 6 | 片段长度 |
| `ngram_repeat` | 3 | 同一片段在本人历史出现几次算违规 |
| `max_per_round` | 1 | 每轮最多重写几条 |

禁句写在每个 speaker 的 `locks.banned_phrases`，主持人的 `banned` 字段会动态追加。

### name_guard / domain_guard / topic_guard

- `name_guard`: 发言里出现"嘿/喂/诶 + 名字"且不在 roster 内 → 重写（防模型凭空造人）
- `domain_guard`: 发言里出现该领域 `avoid` 词 → 重写（防跨域惯性）
- `topic_guard`: 旧话题阶段的禁词 → 重写（配 `topic_plan` 使用）

均为 `{enabled, max_per_round}`。

### 开场白 / 角度 / 角色

| 字段 | 说明 |
|---|---|
| `opener_pools` | `{"角色名": ["开场1", ...]}`，主管每轮随机指定（会避开连续重复） |
| `opener_max_rounds` | 只在前 N 轮发开场牌（默认不限；建议 12） |
| `angles` / `angle_every` | 通用子问题池，每 N 轮换一个（与 domains 二选一） |
| `roles` | 视角池（省钱派/风险派/…），每轮轮转分配 |
| `topic_plan` | `[{topic, rounds, avoid}]`，不由主持人时用固定话题序列 |

## speakers

| 字段 | 说明 |
|---|---|
| `name` | 房间内唯一名字（接力/引用/守卫都靠它） |
| `model` | Ollama tag，必须与 `ollama list` 完全一致 |
| `persona` | 系统提示里的人设；建议写"性格 + 说话方式"，不要把口头禅写死 |
| `temperature` | 采样温度 |
| `locks` | `{banned_phrases, max_chars, no_analogy}` |

`locks.max_chars` 是软上限（提示 + 超过 1.2 倍硬截断）；`no_analogy: true` 会禁止"像/就像/好比"。

## 最小可用配置

```json
{
  "run": {
    "mode": "relay",
    "rounds": 40,
    "domain_rounds": 20,
    "domains": [
      {"name": "哲学", "hint": "自由意志、身份同一性、意识"},
      {"name": "高科技", "hint": "脑机接口、AI 对齐、机器人"}
    ],
    "conductor": {"enabled": true, "every": 8},
    "think": false,
    "max_tokens": 200
  },
  "speakers": [
    {"name": "甲", "model": "qwen2.5:7b-instruct", "persona": "务实的工程师，说话短，爱给具体方案"},
    {"name": "乙", "model": "qwen2.5:3b-instruct", "persona": "谨慎的怀疑者，先问证据"},
    {"name": "丙", "model": "qwen2.5:1.5b-instruct", "persona": "关注成本和失败模式"},
    {"name": "丁", "model": "qwen3:4b", "persona": "爱问傻问题，但常问在点上"}
  ]
}
```

> 提示：`rounds` 先设 10 冒烟，确认每轮 4 条发言、无空回复，再放大。
