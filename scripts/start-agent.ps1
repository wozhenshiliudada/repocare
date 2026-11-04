param(
  [Parameter(Mandatory=$true)][string]$Repo,
  [Parameter(Mandatory=$true)][string]$Policy,
  [Parameter(Mandatory=$true)][string]$RunsDir,
  [Parameter(Mandatory=$true)][string]$ProviderConfig,
  [string]$Python = 'python',
  [int]$Port = 0,
  [int]$RequestBudget = 100,
  [int]$MaxAttempts = 1,
  [switch]$AllowAuto,
  [switch]$Restart
)
$taskArguments = @('-X','utf8',"$PSScriptRoot/start-agent.py",'--repo',$Repo,'--policy',$Policy,
  '--runs-dir',$RunsDir,'--provider-config',$ProviderConfig,'--port',"$Port",
  '--request-budget',"$RequestBudget",'--max-attempts',"$MaxAttempts")
if ($AllowAuto) { $taskArguments += '--allow-auto' }
if ($Restart) { $taskArguments += '--restart' }
& $Python @taskArguments
exit $LASTEXITCODE
