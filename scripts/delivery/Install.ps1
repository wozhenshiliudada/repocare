$ErrorActionPreference = 'Stop'
$deliveryRoot = $PSScriptRoot
$pythonBase = Join-Path $deliveryRoot 'python/python.exe'
& $pythonBase (Join-Path $deliveryRoot 'verify.py')
if ($LASTEXITCODE -ne 0) { throw 'Delivery integrity check failed' }
if (!(Get-Command git -ErrorAction SilentlyContinue) -or !(Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Install Git and supported Node >=22.19.0 <25 first; this kit does not change system tools'
}
$nodeVersionText = & node --version
if ($LASTEXITCODE -ne 0 -or $nodeVersionText -notmatch '^v(\d+)\.(\d+)\.(\d+)$') { throw 'Could not verify Node version' }
$nodeVersion = [version]($nodeVersionText.Substring(1))
if ($nodeVersion -lt [version]'22.19.0' -or $nodeVersion -ge [version]'25.0.0') { throw 'This kit requires Node >=22.19.0 <25' }
$envRoot = Join-Path $deliveryRoot '.venv'
if (Test-Path -LiteralPath $envRoot) { throw 'An environment already exists; keep it and inspect rather than overwrite' }
& $pythonBase -m venv $envRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not create the new local environment' }
$python = Join-Path $envRoot 'Scripts/python.exe'
& $python -m pip install --no-index --find-links (Join-Path $deliveryRoot 'wheels') --require-hashes -r (Join-Path $deliveryRoot 'app/requirements/windows-py313-workbench.lock')
if ($LASTEXITCODE -ne 0) { throw 'Pinned offline dependency installation failed; keep the environment and logs' }
$projectWheel = @(Get-ChildItem -LiteralPath (Join-Path $deliveryRoot 'wheels') -Filter 'repo_maintainer-*.whl')
if ($projectWheel.Count -ne 1) { throw 'Expected one current project wheel' }
& $python -m pip install --no-index --no-deps $projectWheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Project wheel installation failed' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies conflict' }
& $python (Join-Path $deliveryRoot 'app/scripts/doctor.py') --profile dev
if ($LASTEXITCODE -ne 0) { throw 'Runtime check failed' }
Write-Output 'Local offline installation complete. Use Start.ps1 to start explicitly; model retrieval remains off until enabled.'
