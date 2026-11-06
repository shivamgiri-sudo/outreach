# MCN LinkedIn office runner: installer for Windows 10/11 (Windows PowerShell 5.1 or PowerShell 7). The portal serves this
# file with its address and a one-time pairing code filled in; paste the one line the portal shows (Start menu, type
# "PowerShell", open it, paste, Enter):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm '<portal>/api/linkedin-runner/install.ps1?code=XXXXXXXX' | iex"
# Everything goes into your own user folder (%LOCALAPPDATA%\MCN-LinkedIn-Runner); no administrator rights are needed,
# except that winget may ask once to install Node.js:
#   1. Node.js LTS if Node 18+ is missing (winget; otherwise the official Node.js 20 zip from nodejs.org, checksum verified)
#   2. the runner files from the portal, Playwright (npm ci) and its Chromium browser
#   3. connects this computer to the portal with the one-time code (the long-lived token is saved, readable only by you)
#   4. starts the runner now and at every logon of this user (Task Scheduler)
# Set $env:MCN_NO_AUTOSTART = '1' before running to skip step 4.
# Uninstall: powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MCN-LinkedIn-Runner\uninstall.ps1", then press
# Disconnect in the portal.
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Portal = '__PORTAL__'
$Code = '__CODE__'
$NodeVersion = 'v20.18.1'
$Base = Join-Path $env:LOCALAPPDATA 'MCN-LinkedIn-Runner'
$App = Join-Path $Base 'app'
$Runner = Join-Path $App 'tools\linkedin_runner.mjs'
$TaskName = 'MCN LinkedIn office runner'
function Say([string]$Message) { Write-Host "[MCN] $Message" }

try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  New-Item -ItemType Directory -Force -Path $Base | Out-Null
  # Only this user (and SYSTEM) may read the folder: it will hold the runner token and the LinkedIn browser sessions.
  $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  & icacls.exe $Base /inheritance:r /grant:r "*${sid}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null

  # 1. Node.js
  function Find-Node {
    foreach ($candidate in @((Get-Command node.exe -ErrorAction SilentlyContinue).Source, (Join-Path $Base 'node\node.exe'))) {
      if ($candidate -and (Test-Path $candidate)) {
        $major = & $candidate -p "process.versions.node.split('.')[0]"
        if ([int]$major -ge 18) { return $candidate }
      }
    }
    return $null
  }
  $node = Find-Node
  if (-not $node -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    Say 'Installing Node.js LTS with winget (Windows may ask for permission)...'
    & winget.exe install --id OpenJS.NodeJS.LTS --exact --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $node = Find-Node
  }
  if (-not $node) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $file = "node-$NodeVersion-win-$arch.zip"
    Say "Downloading Node.js $NodeVersion from nodejs.org..."
    $tmp = Join-Path $env:TEMP ("mcn-node-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/$NodeVersion/$file" -OutFile (Join-Path $tmp $file)
    $sums = (Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/$NodeVersion/SHASUMS256.txt").Content
    $want = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($file) + '$' } | Select-Object -First 1) -replace '\s.*$', ''
    $got = (Get-FileHash -Algorithm SHA256 (Join-Path $tmp $file)).Hash.ToLower()
    if (-not $want -or $want.Trim().ToLower() -ne $got) { throw 'The Node.js download did not match its checksum; try again.' }
    Expand-Archive -Force (Join-Path $tmp $file) $tmp
    $nodeDir = Join-Path $Base 'node'
    if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
    Move-Item (Join-Path $tmp "node-$NodeVersion-win-$arch") $nodeDir
    Remove-Item -Recurse -Force $tmp
    $node = Join-Path $nodeDir 'node.exe'
  }
  $nodeDir = Split-Path -Parent $node
  $env:Path = "$nodeDir;$env:Path"
  $npm = Join-Path $nodeDir 'npm.cmd'
  $npx = Join-Path $nodeDir 'npx.cmd'
  Say "Node.js $(& $node -v) ($node)"

  # Stop a runner that is already installed (this is also how it is updated)
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*MCN-LinkedIn-Runner*" -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

  # 2. The runner files, Playwright and Chromium
  Say "Downloading the runner from $Portal ..."
  $zip = Join-Path $env:TEMP ("mcn-runner-" + [guid]::NewGuid() + '.zip')
  try { Invoke-WebRequest -UseBasicParsing "$Portal/api/linkedin-runner/bundle.zip?code=$Code" -OutFile $zip }
  catch { throw 'Could not download the runner. The code may have expired (15 minutes): make a new one in the portal.' }
  if (Test-Path (Join-Path $App 'tools')) { Remove-Item -Recurse -Force (Join-Path $App 'tools') }
  New-Item -ItemType Directory -Force -Path $App | Out-Null
  Expand-Archive -Force $zip $App
  Remove-Item -Force $zip
  Push-Location $App
  try {
    Say 'Installing Playwright (this takes a minute)...'
    & $npm ci --omit=dev --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed (see above).' }
    Say 'Installing the Chromium browser for Playwright...'
    & $npx --no-install playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Installing Chromium failed (see above).' }
  } finally { Pop-Location }

  # 3. Connect to the portal
  & $node $Runner --portal $Portal --code $Code --pair-only
  if ($LASTEXITCODE -ne 0) { throw 'Could not connect this computer to the portal (see the message above).' }

  # Starter (no console window; the LinkedIn browser window stays visible) and uninstaller
  $vbs = Join-Path $Base 'start-runner.vbs'
  Set-Content -Encoding ASCII -Path $vbs -Value @(
    'Set shell = CreateObject("WScript.Shell")',
    ('shell.Run """' + $node + '"" ""' + $Runner + '""", 0, False')
  )
  $uninstall = Join-Path $Base 'uninstall.ps1'
  Set-Content -Encoding UTF8 -Path $uninstall -Value @(
    '# Removes the MCN LinkedIn office runner from this computer (the browser sessions too). Then press Disconnect in the portal.',
    "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false -ErrorAction SilentlyContinue",
    'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*MCN-LinkedIn-Runner*" -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }',
    'Start-Sleep -Seconds 2',
    "Remove-Item -Recurse -Force '$Base' -ErrorAction SilentlyContinue",
    'Write-Host "[MCN] The office runner is removed from this computer. Press Disconnect for it in the portal too."'
  )

  # 4. Start now and at every logon of this user
  if ($env:MCN_NO_AUTOSTART -ne '1') {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbs`"" -WorkingDirectory $App
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Say "Registered to start at every logon (Task Scheduler: '$TaskName') and started."
  } else {
    Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`""
    Say 'Started (not registered to start at logon).'
  }

  Say 'Done. In the portal, wait for "Office computer online", then add the LinkedIn account and log it in there.'
  Say 'Keep this computer switched on and connected during working hours, and do not close the LinkedIn browser window.'
  Say "Log: $Base\logs\runner.log   Uninstall: powershell -ExecutionPolicy Bypass -File `"$uninstall`""
} catch {
  Write-Host "[MCN] Installation stopped: $($_.Exception.Message)" -ForegroundColor Red
}
}
