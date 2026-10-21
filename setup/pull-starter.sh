#!/usr/bin/env bash
# Pull the standard models used by configs/starter.json
set -e
for m in qwen2.5:7b-instruct qwen2.5:3b-instruct qwen2.5:1.5b-instruct qwen3:4b; do
  echo "==> ollama pull $m"
  ollama pull "$m"
done
echo "done. run: python supervisor.py --config configs/starter.json"
