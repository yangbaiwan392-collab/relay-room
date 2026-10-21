# RELATED — 与 Carlos Kelkboom 的 Agent 架构、以及其它多模型方案的对比

> 2026-10-15 的 Beyond Coding 采访里，Corepunk 前技术负责人 / 首席架构师 **Carlos Kelkboom**
> 讲了他用"主 agent + 子 agent"做工程的方式。很多人第一反应是"和 Relay Room 很像"。
> 这篇文档说清：**哪里像、哪里不同、哪些能直接借。**

资料来源（二手整理 + 原始视频）：

- 采访视频：[Lead Architect Reveals His Rules for Coding Agents — Beyond Coding](https://www.youtube.com/watch?v=mkN4uRZYPfs)
- 采访整理（含时间戳）：[Coding Agents Need Architecture: Carlos Kelkboom's Rules](https://www.ai.joaoqueiros.com/blog/carlos-kelkboom-coding-agents-scaffolding-worktrees-review)
- 文中提到的 SlopCodeBench 论文（结构性侵蚀，77% 轨迹）：[arxiv.org/abs/2603.24755](https://arxiv.org/abs/2603.24755)

## Carlos 的做法（按时间戳）

1. **先把架构写在纸上**（[11:54](https://www.youtube.com/watch?v=mkN4uRZYPfs&t=714s)）
   手工建目录/文件，先写注释、类型名、函数签名、示例（约 20–60 分钟），把边界与数据流固定下来，
   再让 agent 实现。目的是"在还便宜的时候做决定"。
2. **对话与写入分离**（[58:18](https://www.youtube.com/watch?v=mkN4uRZYPfs&t=3498s)）
   主 agent **不编辑文件、不改机器**：它只收集上下文、追问、把任务磨清楚，然后把边界清晰的活派给子 agent。
3. **并行 worktree 试方案**（[40:12](https://www.youtube.com/watch?v=mkN4uRZYPfs&t=2412s)）
   用 `git worktree` 让多个实现并行、互不覆盖；人看 diff、测试和集成成本再决定合并哪个。
4. **给线索，不给倾倒**（[53:33](https://www.youtube.com/watch?v=mkN4uRZYPfs&t=3213s)）
   上下文入口是"指针"（路径、链接、commit hash），先看 commit/diff 再看整个文件；
   底层是分布式 Markdown + 索引/关键词检索，而不是把整个仓库塞进 prompt。
5. **看不懂就扔**（[1:03:07](https://www.youtube.com/watch?v=mkN4uRZYPfs&t=3787s)）
   即使测试通过，只要他解释不清这段代码，就丢弃；测试是必要条件，不是充分条件。
   确定性检查、变异测试是审查的辅助，不是替代。
6. 其它：架构定下来后他偏好"一次性大任务"（个人选择）；担心团队失去共享语言与带教。

## 和 Relay Room 的相似点

| 原则 | Carlos | Relay Room |
|---|---|---|
| 协调者只思考、不执行 | 主 agent 只读、不写文件 | 云端 conductor 只返回 JSON（角度/事实/任务/禁词），从不直接写发言 |
| 给执行者**有边界**的活 | 先定接口与数据流，再派子任务 | 每轮只给一个角度 + 90 字上限 + 指定视角/任务 |
| 上下文要"线索"不要"倾倒" | 指针 → commit/diff → 文件 | 只递上一位末 100 字 + 最近窗口，而不是整段历史 |
| 审查门 | 人审 diff/测试，看不懂就扔 | guard 自动审（引用/重复/禁句/名字/领域），不合格重写 |
| 并行探索 | 多 worktree 并行实现 | 多 speaker 并行视角 / 可开多房间 |
| 维护性优先于"能跑" | 解释不清就丢弃 | 接不住上一位的发言会被退回重写 |

## 本质差异

| 维度 | Carlos | Relay Room |
|---|---|---|
| 产物 | 代码 / 可维护的系统 | 一场连续的对话 |
| 真值来源 | 测试 + 人的理解 | 代理指标（接续率、重复率）——**没有人类评分** |
| 谁做最终决定 | 人（架构师） | LLM conductor（无人在环） |
| 多 agent 的关系 | 委派 / 并行候选，最后合并一个 | 接力 / 不合并，对话继续 |
| 失败模式 | 看不懂的代码进仓库 | 礼貌附和、复读、各说各的 |
| 上下文载体 | 仓库 + Markdown + 检索 | SQLite 消息总线 + 每轮窗口 |

## 可以直接借过来的 5 件事

1. **"丢弃"而不是"接受"**：现在 relay_guard 重写一次仍不合格就放过。可以试 A/B：
   不合格直接丢弃该发言（或标记 skipped），看整体质量/接续是否更好。这是 Carlos 最硬的一条。
2. **分支房间 = worktree**：同一话题开 2 个并行 run（不同 conductor seed），到检查点由 conductor
   或人挑一条继续。比"多 speaker 视角"更接近他的"并行候选"。
3. **房间宪章（architecture on the page）**：现在规则散在 config 里。可以加一份 `ROOM.md`
   （成员、领域、禁词、目标），启动时作为系统提示的一部分，conductor 只能在这个边界内改角度。
4. **指针对话**：小模型处理不了"commit hash"，但可以传"(第 12 轮，老王，关于记忆的因果链)"这种
   结构化指针，替代部分全文回放，省 context。
5. **人类检查点**：每 N 轮暂停，等人给方向；这也回应他"团队共享语言/带教"的担忧。

## 和别的多模型方案比

| 方案 | 机制 | 和我们的差别 |
|---|---|---|
| AutoGen `RoundRobinGroupChat` | 固定顺序轮转，全员看同一份历史 | 没有"必须接上一位"，实测接续率 ≈2% |
| SillyTavern Group Chats | 按 talkativeness 加权随机选人，全量历史 | 角色扮演向，不保证接续 |
| Multiagent Debate / MoA | 同轮并行互评，最后聚合答案 | 目标是答案正确率，不是对话延续 |
| Chain-of-Agents | 分段接力处理长文本 | 接力的是文档片段，不是对话 |
| Carlos Kelkboom | 人定架构 + 只读主 agent + 有界子 agent + 并行 worktree + 丢弃不可理解代码 | 目标是可维护代码；人是最终审查者 |
| **Relay Room** | 云端 conductor + 串行接力 + 引用校验 + 习惯守卫 | 目标是"像一场对话"；指标化、可观测、全本地 speaker |

一句话：**Carlos 在解决"AI 写的代码人还能不能维护"，我们在解决"AI 之间的对话还算不算对话"。
同一个架构直觉（协调者与执行者分离、有界任务、线索上下文、审查门），落在不同产物上。**
