#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$base = Join-Path $HOME 'SpiderFoot-Final'
$env:SPIDERFOOT_DATA = Join-Path $HOME 'SpiderFoot-Final-Dados'
$env:SPIDERFOOT_CACHE = Join-Path $env:SPIDERFOOT_DATA 'cache'
$env:SPIDERFOOT_LOGS = Join-Path $env:SPIDERFOOT_DATA 'logs'
Set-Location $base
& (Join-Path $HOME 'MinhaExposicao-Runtime-Venv\Scripts\python.exe') .\sf.py -l 127.0.0.1:5004
