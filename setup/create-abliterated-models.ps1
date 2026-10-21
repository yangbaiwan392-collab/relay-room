# Build the four abliterated/heretic Ollama models used by
# configs/tested-4-abliterated-zh.json from local GGUF files.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File setup\create-abliterated-models.ps1 -GgufDir D:\gguf
#
# Expected file names in -GgufDir:
#   Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf
#   InterimLabs-Huihui-Qwen3.5-4B-abliterated-Q4_K_M.gguf   (name may vary; edit the map below)
#   spark-x2.5-4b-heretic-q4_K_M.gguf
#   minicpm5-2b-trial82-q4_k_m.gguf
param(
    [Parameter(Mandatory = $true)][string]$GgufDir
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $GgufDir)) { throw "GgufDir not found: $GgufDir" }

$map = @(
    @{ Model = 'spk-abl-qwen7b';    File = 'Qwen2.5-7B-Instruct-abliterated.Q4_K_M.gguf';      Ctx = 4096; Temp = 0.8;  Rep = 1.15 },
    @{ Model = 'spk-abl-qwen4b';    File = 'InterimLabs-Huihui-Qwen3.5-4B-abliterated-Q4_K_M.gguf'; Ctx = 4096; Temp = 0.9; Rep = 1.15 },
    @{ Model = 'spk-abl-spark4b';   File = 'spark-x2.5-4b-heretic-q4_K_M.gguf';               Ctx = 4096; Temp = 0.9; Rep = 1.15 },
    @{ Model = 'spk-abl-minicpm2b'; File = 'minicpm5-2b-trial82-q4_k_m.gguf';                  Ctx = 4096; Temp = 0.9; Rep = 1.15 }
)

foreach ($item in $map) {
    $path = Join-Path $GgufDir $item.File
    if (-not (Test-Path $path)) {
        Write-Warning "skip $($item.Model): missing $path"
        continue
    }
    $mf = Join-Path $env:TEMP ("Modelfile-" + $item.Model)
    @"
FROM $path
PARAMETER num_ctx $($item.Ctx)
PARAMETER temperature $($item.Temp)
PARAMETER repeat_penalty $($item.Rep)
"@ | Set-Content -LiteralPath $mf -Encoding Ascii
    Write-Host "==> ollama create $($item.Model)"
    & ollama create $item.Model -f $mf
    Remove-Item $mf -Force -ErrorAction SilentlyContinue
}
Write-Host "done. check: ollama list"
