# MCN LinkedIn office runner: installer for Windows 10/11 (Windows PowerShell 5.1 or PowerShell 7). The portal serves this
# file with its address and a one-time pairing code filled in; paste the one line the portal shows (Start menu, type
# "PowerShell", open it, paste, Enter):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; irm '<portal>/api/linkedin-runner/install.ps1?code=XXXXXXXX' | iex"
# (the TLS part is needed on Windows PowerShell 5.1, which offers only TLS 1.0 by default).
# Everything goes into your own user folder (%LOCALAPPDATA%\MCN-LinkedIn-Runner); no administrator rights are needed:
#   1. Node.js: the computer's own Node 18-24 if it has one, else the official Node.js 22 zip from nodejs.org (checksum
#      verified) inside that folder. Not winget: it installs the newest LTS, and Node 26 hangs Playwright 1.49's
#      Chromium install for ever (found on the first real Windows run).
#   2. the runner files from the portal, Playwright (npm ci) and its Chromium browser
#   3. connects this computer to the portal with the one-time code (the long-lived token is saved, readable only by you)
#   4. starts the runner now and at every logon of this user (Task Scheduler; if that is not allowed on this computer, a
#      launcher in the user's Startup folder instead)
# Set $env:MCN_NO_AUTOSTART = '1' before running to skip step 4.
# Uninstall: powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MCN-LinkedIn-Runner\uninstall.ps1", then press
# Disconnect in the portal.
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Portal = '__PORTAL__'
$Code = '__CODE__'
$NodeVersion = 'v22.12.0'
$Base = Join-Path $env:LOCALAPPDATA 'MCN-LinkedIn-Runner'
$App = Join-Path $Base 'app'
$Runner = Join-Path $App 'tools\linkedin_runner.mjs'
$TaskName = 'MCN LinkedIn office runner'
$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'MCN LinkedIn office runner.vbs'
function Say([string]$Message) { Write-Host "[MCN] $Message" }
# Same bytes on Windows PowerShell 5.1 and PowerShell 7 (Set-Content -Encoding differs between them): .ps1 as UTF-8 with
# BOM so 5.1 reads non-ASCII user folders right, .vbs as UTF-16 LE (WSH reads ANSI or UTF-16 only, never UTF-8).
function Write-Text([string]$Path, [string[]]$Lines, [System.Text.Encoding]$Encoding) {
  [IO.File]::WriteAllText($Path, (($Lines -join "`r`n") + "`r`n"), $Encoding)
}
function Quote-Single([string]$Text) { "'" + ($Text -replace "'", "''") + "'" }   # a PowerShell literal, safe for O'Brien

try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  New-Item -ItemType Directory -Force -Path $Base | Out-Null
  # Only this user (and SYSTEM) may read the folder: it will hold the runner token and the LinkedIn browser sessions.
  $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  & icacls.exe $Base /inheritance:r /grant:r "*${sid}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null

  # 1. Node.js
  # Playwright 1.49 (deploy/linkedin-runtime) is tested up to Node 24; a newer system Node gets our private Node instead.
  function Find-Node {
    foreach ($candidate in @((Get-Command node.exe -ErrorAction SilentlyContinue).Source, (Join-Path $Base 'node\node.exe'))) {
      if ($candidate -and (Test-Path $candidate)) {
        $major = & $candidate -p "process.versions.node.split('.')[0]"
        if ([int]$major -ge 18 -and [int]$major -le 24) { return $candidate }
        Say "Not using Node.js $(& $candidate -v) at $candidate (the runner needs Node 18 to 24)."
      }
    }
    return $null
  }
  $node = Find-Node
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
  # npm/npx next to node.exe (nodejs.org, winget, nvm); version managers with shims (Volta, Scoop) keep them elsewhere
  $npm = Join-Path $nodeDir 'npm.cmd'
  if (-not (Test-Path $npm)) { $npm = (Get-Command npm.cmd, npm.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
  $npx = Join-Path $nodeDir 'npx.cmd'
  if (-not (Test-Path $npx)) { $npx = (Get-Command npx.cmd, npx.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
  if (-not $npm -or -not $npx) { throw "Node.js was found ($node) but npm/npx were not. Reinstall Node.js LTS from nodejs.org and run this again." }
  Say "Node.js $(& $node -v) ($node)"

  # Stop a runner that is already installed (this is also how it is updated)
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*MCN-LinkedIn-Runner*" -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

  # 2. The runner files, Playwright and Chromium
  Say "Downloading the runner from $Portal ..."
  $zip = Join-Path $env:TEMP ("mcn-runner-" + [guid]::NewGuid() + '.zip')
  try { Invoke-WebRequest -UseBasicParsing "$Portal/api/linkedin-runner/bundle.zip?code=$Code" -OutFile $zip }
  catch { throw "Could not download the runner ($($_.Exception.Message)). The code may have expired (15 minutes): make a new one in the portal." }
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
  $cfg = Join-Path $Base 'config.json'
  $before = if (Test-Path -LiteralPath $cfg) { Get-Content -Raw -LiteralPath $cfg } else { '' }   # a re-install's old token
  & $node $Runner --portal $Portal --code $Code --pair-only
  if ($LASTEXITCODE -ne 0) {
    # Node on Windows can crash while exiting (libuv assertion) AFTER the pairing worked: a NEW saved token decides (an
    # earlier install's token left in config.json must not make a failed pairing look like a success).
    $paired = $false
    if (Test-Path -LiteralPath $cfg) {
      try { $now = Get-Content -Raw -LiteralPath $cfg; $paired = ($now -ne $before) -and [bool](($now | ConvertFrom-Json).token) } catch { }
    }
    if (-not $paired) { throw 'Could not connect this computer to the portal (see the message above).' }
    Say 'Connected (Node stopped untidily after saving the token; that is harmless).'
  }

  # Starter (no console window; the LinkedIn browser window stays visible) and uninstaller
  $vbs = Join-Path $Base 'start-runner.vbs'
  $vbsLines = @(
    'Set shell = CreateObject("WScript.Shell")',
    ('shell.Run """' + $node + '"" ""' + $Runner + '""", 0, False')
  )
  Write-Text $vbs $vbsLines ([System.Text.Encoding]::Unicode)
  $uninstall = Join-Path $Base 'uninstall.ps1'
  Write-Text $uninstall @(
    '# Removes the MCN LinkedIn office runner from this computer (the browser sessions too). Then press Disconnect in the portal.',
    "Unregister-ScheduledTask -TaskName $(Quote-Single $TaskName) -Confirm:`$false -ErrorAction SilentlyContinue",
    "Remove-Item -Force -LiteralPath $(Quote-Single $StartupLink) -ErrorAction SilentlyContinue",
    'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*MCN-LinkedIn-Runner*" -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }',
    'Start-Sleep -Seconds 2',
    "Remove-Item -Recurse -Force -LiteralPath $(Quote-Single $Base) -ErrorAction SilentlyContinue",
    'Write-Host "[MCN] The office runner is removed from this computer. Press Disconnect for it in the portal too."'
  ) (New-Object System.Text.UTF8Encoding $true)

  # 4. Start now and at every logon of this user
  Remove-Item -Force -LiteralPath $StartupLink -ErrorAction SilentlyContinue      # from an earlier install's fallback
  if ($env:MCN_NO_AUTOSTART -ne '1') {
    # The account name as Windows knows it (USERDOMAIN\USERNAME can differ for Microsoft / Entra ID accounts)
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    try {
      $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbs`"" -WorkingDirectory $App
      $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
      $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
      $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
      Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
      Start-ScheduledTask -TaskName $TaskName
      Say "Registered to start at every logon (Task Scheduler: '$TaskName') and started."
    } catch {
      # The computer is paired already (the code is used up), so do not stop here: start from the Startup folder instead.
      Say "Task Scheduler refused ($($_.Exception.Message)); using the Startup folder instead."
      Write-Text $StartupLink $vbsLines ([System.Text.Encoding]::Unicode)
      Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`""
      Say "Started; it will also start at every logon ($StartupLink)."
    }
  } else {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue   # an earlier install's autostart
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
