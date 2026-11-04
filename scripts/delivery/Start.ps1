$ErrorActionPreference = 'Stop'
$deliveryRoot = $PSScriptRoot
$python = Join-Path $deliveryRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Run Install.ps1 first' }
& $python (Join-Path $deliveryRoot 'app/maintain.py') web --semantic-model-manifest (Join-Path $deliveryRoot 'model/manifest.json') @args
exit $LASTEXITCODE
