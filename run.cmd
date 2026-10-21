@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not defined CONFIG set CONFIG=configs\starter.json
python supervisor.py --config "%CONFIG%" %*
