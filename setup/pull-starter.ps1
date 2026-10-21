# Pull the standard models used by configs/starter.json
$ErrorActionPreference = 'Stop'
foreach ($m in @('qwen2.5:7b-instruct','qwen2.5:3b-instruct','qwen2.5:1.5b-instruct','qwen3:4b')) {
    Write-Host "==> ollama pull $m"
    & ollama pull $m
}
Write-Host "done. run: python supervisor.py --config configs/starter.json"
