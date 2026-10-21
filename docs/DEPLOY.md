# 部署指南（Deploy）

> 目标：从零到"4 个本地模型在同一个房间里接力聊天"，约 20 分钟。
> 环境：Windows 10/11（主力）、Linux/macOS（可用，未充分实测）；Python ≥ 3.10；Ollama ≥ 0.4；
> 一张 12 GB 以上显存的消费级显卡（16 GB 可跑 4 个 4–7B）。

## 0. 检查清单

```bash
python --version          # ≥ 3.10
ollama --version          # ≥ 0.4
nvidia-smi                # 显存 ≥ 12 GB（16 GB 推荐）
```

不需要 `pip install` 任何东西——本项目只用 Python 标准库。

## 1. 启动 Ollama

Windows 图形版：运行 `ollama app.exe`。命令行/服务：

```bash
ollama serve
```

确认：

```bash
curl http://127.0.0.1:11434/api/tags
```

> 如果之前改过 `OLLAMA_MAX_LOADED_MODELS`，注意：`setx` / 用户环境变量**对已运行的 Ollama
> 不生效**，必须在启动 Ollama 的进程里设置再重启。默认并发加载数 = 3×GPU 数，4 个 speaker
> 建议 `OLLAMA_MAX_LOADED_MODELS=4` 或更高（见 PITFALLS #9）。

## 2. 准备模型

### 方案 A：标准模型（最快，适合先跑通）

```bash
ollama pull qwen2.5:7b-instruct
ollama pull qwen2.5:3b-instruct
ollama pull qwen2.5:1.5b-instruct
ollama pull llama3.2:3b-instruct
```

然后用 `configs/starter.json`。这组是"对齐过"的模型，中文日常话题没问题；成人/争议话题会被
拒答或软化——这是模型本身的行为，不是框架限制。

### 方案 B：本仓库实测的去审查模型（效果最好，需要下载 GGUF）

实测配置用的是 4 个 abliterated / heretic 模型（4–7B）：

| 角色 | 模型 | 来源 | 大小 |
|---|---|---|---|
| 老王 | Qwen2.5-7B-Instruct-abliterated Q4_K_M | [mradermacher/Qwen2.5-7B-Instruct-abliterated-GGUF](https://huggingface.co/mradermacher/Qwen2.5-7B-Instruct-abliterated-GGUF) | 4.36 GB |
| 阿May | huihui Qwen3.5-4B-abliterated Q4_K_M | [sachasayan/interimlabs-huihui-qwen3.5-4b-abliterated](https://huggingface.co/sachasayan/interimlabs-huihui-qwen3.5-4b-abliterated) | 2.7 GB |
| 大刘 | spark-x2.5-4B-heretic Q4_K_M | [maternion/spark-x2.5-heretic](https://huggingface.co/maternion/spark-x2.5-heretic) | 2.6 GB |
| 小陈 | minicpm5-2B-heretic Q4_K_M | [maternion/minicpm5-heretic](https://huggingface.co/maternion/minicpm5-heretic) | 1.6 GB |

下载 GGUF 后逐个建模型（Windows PowerShell）：

```powershell
# 示例：把 <path> 换成 GGUF 实际路径
@"
FROM <path>\Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf
PARAMETER num_ctx 4096
PARAMETER temperature 0.8
PARAMETER repeat_penalty 1.15
"@ | Set-Content -Encoding Ascii modelfile.txt
ollama create spk-abl-qwen7b -f modelfile.txt
```

其余三个同理，命名成 `spk-abl-qwen7b` / `spk-abl-qwen4b` / `spk-abl-spark4b` / `spk-abl-minicpm2b`，
或者直接改 `configs/tested-4-abliterated-zh.json` 里的 `model` 字段。

> 国内下载建议用 `hf-mirror.com` 镜像，单文件 2–4 GB，注意校验 SHA256（HF API 的
> `lfs.oid` 就是 sha256）。
> 法律/合规：abliterated 模型会移除部分安全对齐；请在你所在地区法律与平台政策允许的范围内使用。

## 3. 配置云端主持人（唯一需要联网的组件）

任意 OpenAI 兼容接口都行（DeepSeek、OpenAI、Moonshot、本地 vLLM 等）：

```bash
# Linux/macOS
export CONDUCTOR_API_KEY=sk-xxxx
export CONDUCTOR_URL=https://api.deepseek.com/chat/completions   # 可选
export CONDUCTOR_MODEL=deepseek-flash                            # 可选

# Windows PowerShell
$env:CONDUCTOR_API_KEY="sk-xxxx"
```

- 密钥**只从环境变量读取**，不会写文件、不会进日志（见 SECURITY.md）。
- 不想用云端？把 `run.conductor.enabled` 设为 `false`。房间仍能接力，但会更快进入"礼貌附和"
  状态——主持人负责供给新内容（见 RESULTS.md 的对照）。
- 费用参考：每 8 轮调用一次，200 轮约 24 次、3 万 tokens，单次几分钱人民币。

## 4. 第一次运行

```bash
cd relay-room
python supervisor.py --config configs/starter.json      # 或 configs/tested-4-abliterated-zh.json
```

另开一个终端看实时对话：

```bash
python chat-view.py
```

Windows 可直接双击 `run.cmd` / `show-chat.cmd`。

验证是否正常：

```bash
python tools/progress.py runs/<run_id>
# round=…   utterances=…   switches=…   dedups=…
```

- `utterances` 应每轮 +4（4 个 speaker）
- `dedups` 每 100 轮约 30–100 次属正常（守卫在工作）
- 如果 `utterances` 不涨：看 `runs/<id>/` 下各 speaker 日志或 `events` 表
  （`python tools/query.py runs/<id> events | tail`）

## 5. 运行中观测

| 命令 | 用途 |
|---|---|
| `python chat-view.py` | 实时对话窗口（自动跟随最新 run） |
| `python dashboard.py` | 只读统计面板（轮次/状态/事件） |
| `python tools/progress.py runs/<id>` | 进度与异常快照 |
| `python tools/relay-analyze.py runs/<id>` | 接续率/回应率/长度 |
| `python tools/dup-view.py runs/<id>` | 逐字重复率（原始 vs 替换后） |
| `python tools/query.py runs/<id> 25` | 看第 25 轮全文 |
| `python tools/summarize-run.py runs/<id>` | 事件计数（改写/守卫/主持人） |

## 6. 常见故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 所有发言是 `(model call failed: URLError)` | 系统代理挂了，urllib 走了死代理 | 本项目已内置 `ProxyHandler({})` 绕过；若仍失败，检查 `HTTP_PROXY/HTTPS_PROXY` |
| 发言为空 / `(empty reply)` | 思考型模型把 token 全花在 thinking | 已默认 `think:false` + 空回复重试；也可加大 `max_tokens` |
| 每轮只有 0–1 条发言 | 上一版 superseded 过滤误伤（已修）；或模型超时 | 看 `progress.py` 的 latency；`turn_timeout` 调大 |
| 显存爆 / 疯狂换页 | 模型总量 + KV 超显存 | 减 speaker、降 `num_ctx`、或换更小模型 |
| 主持人一直 `unparsable` | 输出被截断/带解释 | 已内置 1500 tokens + 逐字段正则兜底 + 重试；仍失败就换模型 |
| 话题 10 轮后就复读 | 主持人没开 / 间隔太长 | 打开 conductor，`every` 设 4–8 |
| 回复越来越长 | 接力传递太多 | 用默认的"末 100 字 + 90 字上限" |

更多真实踩坑记录：[PITFALLS.md](PITFALLS.md)。

## 7. 从零做一次"验收"

```bash
# 200 轮，四域（哲学/玄学/高科技/成人），每域 50 轮，主持人每 8 轮
python supervisor.py --config configs/tested-4-abliterated-zh.json

python tools/relay-analyze.py runs/<id>      # 期望 continuity ≥ 35%
python tools/dup-view.py runs/<id>           # 期望 exact dup ≤ 1%
python tools/phase-continuity.py runs/<id>   # 每 50 轮一段，不应随长度衰减
```

参考基线：接续率 38%、回应率 66%、逐字重复 0.1%、改写率 81%、200 轮约 33 分钟
（RTX 5060 Ti 16 GB，4 个模型全部驻留 GPU）。
