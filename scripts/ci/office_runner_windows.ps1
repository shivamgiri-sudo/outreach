# CI only (.github/workflows/office-runner-windows.yml): drives the office-runner Windows installer end to end against a
# throwaway local portal, the way an office user would: make a pairing code like the portal's "Connect an office computer"
# button, paste the exact one-line command it shows into this PowerShell host, check the result, install again over the
# running copy, then run the uninstall command the portal shows.
# The portal has no LinkedIn accounts, so the runner never starts a browser and never opens linkedin.com.
# Runs under Windows PowerShell 5.1 or PowerShell 7; -Shell picks which program the pasted one-liner launches.
param(
  [string]$Portal = 'http://127.0.0.1:8000',
  [ValidateSet('powershell', 'pwsh')][string]$Shell = 'powershell',
  [string]$LogDir = (Join-Path $env:RUNNER_TEMP 'office-runner-logs'),
  [switch]$HideNode              # an office PC without Node.js: the installer must fetch it (winget or the nodejs.org zip)
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Base = Join-Path $env:LOCALAPPDATA 'MCN-LinkedIn-Runner'
$TaskName = 'MCN LinkedIn office runner'
$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'MCN LinkedIn office runner.vbs'
$script:Failures = @()

function Step([string]$Text) { Write-Host "`n===== $Text =====" -ForegroundColor Cyan }
function Check([bool]$Ok, [string]$What) {
  if ($Ok) { Write-Host "  PASS  $What" -ForegroundColor Green }
  else { Write-Host "  FAIL  $What" -ForegroundColor Red; $script:Failures += $What }
}
function Api([string]$Method, [string]$Path, $Body = $null) {
  $headers = @{ Authorization = "Bearer $script:Token" }
  $json = if ($null -ne $Body) { $Body | ConvertTo-Json -Compress } else { $null }
  Invoke-RestMethod -UseBasicParsing -Method $Method -Uri "$Portal$Path" -Headers $headers -ContentType 'application/json' -Body $json
}
function Runner-Processes {
  @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*MCN-LinkedIn-Runner*linkedin_runner.mjs*' })
}
function Wait-Online([string]$NotRunnerId = '', [int]$Seconds = 180) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    $view = Api GET '/api/linkedin-cloud'
    if ($view.runner -and $view.runner.online -and $view.runner.id -ne $NotRunnerId -and $view.runner.version) { return $view }
    Start-Sleep -Seconds 5
  } while ((Get-Date) -lt $deadline)
  return $view
}
function Paste([string]$Command, [string]$LogName) {
  # What the user does: paste the line into a PowerShell window. For the PowerShell 7 run the line launches pwsh instead.
  if ($Shell -eq 'pwsh') { $Command = $Command -replace '^powershell ', 'pwsh ' }
  Write-Host "  > $Command"
  $log = Join-Path $LogDir $LogName
  $ErrorActionPreference = 'Continue'      # native stderr (npm warnings) must not abort the driver on Windows PowerShell 5.1
  $output = @(Invoke-Expression $Command 2>&1 | ForEach-Object { "$_" } | ForEach-Object { Write-Host $_; $_ })
  [IO.File]::WriteAllLines($log, [string[]]$output)
  return ($output -join "`n")
}
function Install-Once([string]$Label, [bool]$Replace, [string]$OldRunnerId) {
  Step "$Label`: make a pairing code (as the portal's Connect an office computer button does)"
  $made = Api POST '/api/linkedin-cloud/runner/pair' @{ replace = $Replace }
  Check ($made.code -match '^[A-Z2-9]{8}$') "$Label`: pairing code made"
  Check ($made.commands.windows -like "powershell -NoProfile -ExecutionPolicy Bypass -Command *$Portal/api/linkedin-runner/install.ps1?code=$($made.code)*") "$Label`: the portal shows the Windows one-liner"
  Step "$Label`: paste the one-liner ($Shell)"
  $out = Paste $made.commands.windows "install-$Label.log"
  Check ($out -notmatch 'Installation stopped') "$Label`: installer did not stop with an error"
  Check ($out -match '\[MCN\] Done\.') "$Label`: installer printed Done"
  # Node 24 on Windows could crash while exiting right after pairing (libuv assertion, src\win\async.c); the runner must
  # now exit cleanly, so the installer's 'stopped untidily' fallback must not be needed here.
  Check ($out -notmatch 'Assertion failed|untidily') "$Label`: pairing step exited cleanly (no libuv assertion on exit)"
  Check (Test-Path (Join-Path $Base 'app\tools\linkedin_runner.mjs')) "$Label`: runner files in $Base\app"
  Check (Test-Path (Join-Path $Base 'app\node_modules\playwright')) "$Label`: Playwright installed"
  $chromium = @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'ms-playwright') -Directory -Filter 'chromium-*' -ErrorAction SilentlyContinue)
  Check ($chromium.Count -gt 0) "$Label`: Playwright's Chromium downloaded ($($chromium.Name -join ', '))"
  if ($HideNode -and $Label -eq 'install1') { Check ($out -match 'Installing Node.js LTS with winget|Downloading Node.js') "$Label`: the installer fetched Node.js itself" }
  $config = Join-Path $Base 'config.json'
  Check (Test-Path $config) "$Label`: runner config written"
  if (Test-Path $config) {
    $c = Get-Content -Raw $config | ConvertFrom-Json
    Check ($c.portal -eq $Portal -and $c.token -like 'mcnr_*' -and $c.runner_id) "$Label`: config has the portal, a runner token and id"
    $acl = (Get-Acl $Base).Access | ForEach-Object { $_.IdentityReference.Value }
    Write-Host "  folder ACL: $($acl -join ', ')"
    Check (-not ($acl | Where-Object { $_ -match 'Users$|Everyone|Authenticated Users' })) "$Label`: install folder is private (no Users/Everyone access)"
  }
  Check (Test-Path (Join-Path $Base 'uninstall.ps1')) "$Label`: uninstall.ps1 written"
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Check ($null -ne $task -or (Test-Path $StartupLink)) "$Label`: autostart registered (scheduled task or Startup launcher)"
  if ($task) { Write-Host "  task state: $($task.State); principal $($task.Principal.UserId) / $($task.Principal.LogonType)" }
  if (Test-Path $StartupLink) { Write-Host "  using the Startup folder launcher: $StartupLink" }
  Step "$Label`: wait for the runner to come online in the portal"
  $view = Wait-Online $OldRunnerId
  Write-Host ("  runner: " + ($view.runner | ConvertTo-Json -Compress -Depth 4))
  Check ([bool]($view.runner -and $view.runner.online -and $view.runner.id -ne $OldRunnerId)) "$Label`: the portal shows the new office computer online (heartbeat)"
  Check (@($view.slots).Count -eq 0) "$Label`: no LinkedIn accounts in the CI portal (nothing opens linkedin.com)"
  Start-Sleep -Seconds 10        # a second copy started by both the task and the fallback would show up by now
  $procs = @(Runner-Processes)
  $procs | ForEach-Object { Write-Host "  runner process $($_.ProcessId): $($_.CommandLine)" }
  Check ($procs.Count -eq 1) "$Label`: exactly one runner process on this computer"
  $logs = Join-Path $Base 'logs'
  if (Test-Path $logs) { Copy-Item -Recurse -Force $logs (Join-Path $LogDir "runner-logs-$Label") }
  return $view.runner.id
}

function Start-Portal {
  # In this step, not an earlier one: Actions ends a step's background processes when the step finishes.
  $bytes = New-Object byte[] 24
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $password = [Convert]::ToBase64String($bytes)
  $env:OUTREACH_ADMIN_PASSWORD = $password                     # throwaway, this job only
  $env:OUTREACH_DATA_FILE = Join-Path $env:RUNNER_TEMP 'portal\store.json'    # not in the uploaded logs
  New-Item -ItemType Directory -Force -Path (Split-Path $env:OUTREACH_DATA_FILE) | Out-Null
  if (Test-Path $env:OUTREACH_DATA_FILE) { Remove-Item -Force $env:OUTREACH_DATA_FILE }
  if (-not $env:OUTREACH_BASE_URL) { $env:OUTREACH_BASE_URL = $Portal }
  $script:Api = Start-Process -FilePath python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1' `
    -WorkingDirectory (Resolve-Path (Join-Path $PSScriptRoot '..\..')) -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir 'api.out.log') -RedirectStandardError (Join-Path $LogDir 'api.err.log')
  for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-RestMethod -UseBasicParsing "$Portal/api/auth/status").auth_required) { Write-Host 'Portal is up (login required).'; return } } catch { }
    Start-Sleep -Seconds 1
  }
  Get-Content (Join-Path $LogDir 'api.err.log') -ErrorAction SilentlyContinue | Write-Host
  throw 'The portal did not start.'
}

try {
  Start-Portal
  if ($HideNode) {
    $kept = $env:Path -split ';' | Where-Object { $_ -and -not (Test-Path (Join-Path $_ 'node.exe')) }
    $env:Path = $kept -join ';'
    Write-Host "Node.js hidden from PATH; winget: $([bool](Get-Command winget.exe -ErrorAction SilentlyContinue))"
  }
  Write-Host "Driver host: PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition)); one-liner host: $Shell"
  Write-Host "User: $([Security.Principal.WindowsIdentity]::GetCurrent().Name); admin: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)); session: $((Get-Process -Id $PID).SessionId)"
  Step 'Log in to the throwaway portal'
  $login = Invoke-RestMethod -UseBasicParsing -Method POST -Uri "$Portal/api/login" -ContentType 'application/json' -Body (@{ password = $env:OUTREACH_ADMIN_PASSWORD } | ConvertTo-Json)
  $script:Token = $login.token
  Check ([bool]$script:Token) 'logged in as the owner'
  $view = Api GET '/api/linkedin-cloud'
  Check ($view.where -eq 'office' -and @($view.slots).Count -eq 0 -and -not $view.runner) 'fresh portal: office mode, no accounts, no runner'

  $since = Get-Date
  $first = Install-Once 'install1' $false ''

  Step 'Disconnect in the portal: the runner stops by itself and forgets its token'
  Api DELETE "/api/linkedin-cloud/runner/$first" | Out-Null
  $deadline = (Get-Date).AddSeconds(90)
  while (@(Runner-Processes).Count -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
  Check (@(Runner-Processes).Count -eq 0) 'runner process exited after Disconnect'
  $log = Get-Content -Raw (Join-Path $Base 'logs\runner.log') -ErrorAction SilentlyContinue
  Check ($log -match 'disconnected in the portal') 'runner log says it was disconnected'
  Check ($log -notmatch 'Assertion failed') 'runner exited without a libuv assertion'
  Check (-not (Get-Content -Raw (Join-Path $Base 'config.json') | ConvertFrom-Json).token) 'runner token deleted from config.json'
  Check (-not (Test-Path (Join-Path $Base 'runner.lock'))) 'runner lock released'
  $logs = Join-Path $Base 'logs'
  if (Test-Path $logs) { Copy-Item -Recurse -Force $logs (Join-Path $LogDir 'runner-logs-disconnect') }

  # Connecting the same computer again over the stopped install.
  $second = Install-Once 'install2' $false $first
  # Installing again while it runs (how it is updated, or when someone pastes a new line): the portal asks to replace.
  $third = Install-Once 'install3' $true $second
  Check ($third -and $third -ne $second) 're-install replaced the office computer record'

  Step 'Uninstall with the command the portal shows'
  $made = Api POST '/api/linkedin-cloud/runner/pair' @{ replace = $true }
  Api DELETE '/api/linkedin-cloud/runner/pair' | Out-Null          # only wanted the uninstall line; cancel the code
  $out = Paste $made.uninstall.windows 'uninstall.log'
  Check ($out -match 'removed from this computer') 'uninstall printed its message'
  Start-Sleep -Seconds 3
  Check ($null -eq (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) 'scheduled task removed'
  Check (-not (Test-Path $StartupLink)) 'Startup launcher removed'
  Check (-not (Test-Path $Base)) "install folder $Base removed"
  Check (@(Runner-Processes).Count -eq 0) 'no runner process left'
  Start-Sleep -Seconds 95                                           # past ONLINE_SECONDS (90)
  $view = Api GET '/api/linkedin-cloud'
  Check ([bool]($view.runner -and -not $view.runner.online)) 'the portal shows the office computer offline after uninstall'
  $crashes = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'Application Error'; StartTime = $since } -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match 'node\.exe' })
  $crashes | ForEach-Object { Write-Host "  crash: $($_.Message -split "`n" | Select-Object -First 3)" }
  Check ($crashes.Count -eq 0) 'no node.exe crash in the Windows event log during the run'
} catch {
  Check $false "driver error: $($_.Exception.Message) at $($_.InvocationInfo.PositionMessage)"
} finally {
  if ($script:Api) { Stop-Process -Id $script:Api.Id -Force -ErrorAction SilentlyContinue }
  $runnerLog = Join-Path $Base 'logs'
  if (Test-Path $runnerLog) { Copy-Item -Recurse -Force $runnerLog (Join-Path $LogDir 'runner-logs') }
}

if ($script:Failures.Count) {
  Write-Host "`n$($script:Failures.Count) check(s) failed:" -ForegroundColor Red
  $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
  exit 1
}
Write-Host "`nAll checks passed ($Shell)." -ForegroundColor Green
