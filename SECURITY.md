# SECURITY & PRIVACY

## 密钥

- 本仓库**不包含任何密钥**。
- conductor 只从环境变量读取：
  - `CONDUCTOR_API_KEY`（必需，或 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`）
  - `CONDUCTOR_URL`（可选，任意 OpenAI 兼容 `/chat/completions` 端点）
  - `CONDUCTOR_MODEL`（可选）
- 为了本机方便，`conductor.py` 还会尝试读 `~/.workbuddy/models.json` 里的 `apiKey`；这是历史遗留的
  便利路径，**不要把该文件复制进仓库或容器镜像**。
- 不要把密钥写进 config、脚本、命令行参数（会进 shell 历史 / 进程列表 / 崩溃日志）。

## 什么离开本机

- **speaker 全部本地**：4 个 Ollama 模型不产生任何出站请求。
- **conductor** 会把以下内容发给云端：
  - 当前话题 / 领域提示
  - 最近 8 条发言（每条截断到 120 字）
  - 已用角度、去重率、房间成员名
- 因此：**不要在不信任的云端接口上跑含敏感内容的房间**。需要完全离线时，把
  `run.conductor.enabled` 设为 `false`（质量会下降，见 RESULTS），或把 `CONDUCTOR_URL` 指向
  你自己部署的本地/内网 OpenAI 兼容服务。

## 本地数据

- 每个 run 在 `runs/<run_id>/` 下生成 `bus.sqlite`（全部对话与守卫事件）和 `config.json`。
- `runs/` 已在 `.gitignore` 中；分享截图/日志前注意其中可能含对话内容。
- `conductor-usage.jsonl` 记录每次调用的 token 用量与返回的 hint（不含密钥）。

## 模型与合规

- 文档中提到的 abliterated / heretic 模型移除了部分安全对齐，可能生成争议或成人内容。
- 请在你所在地区的法律、模型许可证（Llama/Qwen 等各有自己的 license）和平台政策允许范围内使用。
- 本项目只提供编排机制，不对模型输出负责；生产环境请自行加审核层。
