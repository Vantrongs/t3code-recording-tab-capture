# Downloads Chrome for Testing and chrome-headless-shell 156.0.8078.70 (win64) into a temporary folder, records
# with bench.py (screencast as T3 does it vs tab capture with a cursor), GPU off/on, three sizes, 60/30 fps, two
# rounds, prints one JSON line per run, and removes the folder.
param([string]$Payload)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
# Invoke-WebRequest's progress bar slows a 200 MB download to minutes in PowerShell 5.
$ProgressPreference = "SilentlyContinue"
# A run cut off from the host leaves its folder behind.
Get-ChildItem $env:TEMP -Directory -Filter "t3-recbench-*" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$work = Join-Path $env:TEMP ("t3-recbench-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory $work | Out-Null
try {
  $base = "https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.70/win64"
  foreach ($name in "chrome-win64", "chrome-headless-shell-win64") {
    Invoke-WebRequest "$base/$name.zip" -OutFile "$work\$name.zip" -UseBasicParsing
    Expand-Archive "$work\$name.zip" -DestinationPath "$work\$name"
    Remove-Item "$work\$name.zip"
  }
  $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1)
  $gpu = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join " | "
  Write-Output ("HOST " + (@{ cpu = $cpu.Name; threads = $cpu.NumberOfLogicalProcessors; gpu = $gpu; os = (Get-CimInstance Win32_OperatingSystem).Caption } | ConvertTo-Json -Compress))
  # uv reports downloads on stderr; with "Stop", PowerShell 5 turns that into a terminating error.
  $ErrorActionPreference = "Continue"
  $uv = @("run", "--no-project", "--python", "3.12", "--with", "psutil==7.2.2", "--with", "playwright>=1.55,<2", "python", "$Payload\bench.py")
  $smoke = & uv @uv tab off 800 450 30 "$work\chrome-win64" "$work\chrome-headless-shell-win64" 2>&1 | ForEach-Object { "$_" }
  $smoke | ForEach-Object { "SMOKE $_" }
  if (-not ($smoke | Where-Object { $_.StartsWith("{") })) { throw "the tab-capture smoke run gave no result" }
  if ($Payload -and (Test-Path "$Payload\gpu.txt")) {
    # GPU study: flag variants, encoders, engine utilization (gpu.py).
    Write-Output ("POWER " + ((powercfg /getactivescheme) -join " "))
    Get-CimInstance Win32_VideoController | ForEach-Object { Write-Output ("ADAPTER " + (@{ name = $_.Name; driver = $_.DriverVersion; pnp = $_.PNPDeviceID; status = $_.Status } | ConvertTo-Json -Compress)) }
    & uv run --no-project --python 3.12 --with psutil==7.2.2 --with pywin32 --with "playwright>=1.55,<2" python "$Payload\gpu.py" "$work\chrome-win64" "$Payload\gpu.txt" 2>&1 | ForEach-Object { "$_" }
    if (Test-Path "$Payload\screencast.txt") {
      # T3's screencast path at the size in screencast.txt (viewport W H), GPU off/on, twice.
      $w, $h = (Get-Content "$Payload\screencast.txt").Trim() -split " "
      $uvg = @("run", "--no-project", "--python", "3.12", "--with", "psutil==7.2.2", "--with", "pywin32", "--with", "playwright>=1.55,<2", "python", "$Payload\bench.py")
      for ($r = 0; $r -lt 2; $r++) {
        foreach ($g in "off", "on") {
          & uv @uvg screencast $g $w $h 60 "$work\chrome-win64" "$work\chrome-headless-shell-win64" 2>&1 | ForEach-Object { "$_" }
        }
      }
    }
    return
  }
  if ($Payload -and (Test-Path "$Payload\repeat.txt")) {
    # Repeats with a fresh browser per run: GPU on/off x overlay, 3200x1800 frames at 60.
    $uvg = @("run", "--no-project", "--python", "3.12", "--with", "psutil==7.2.2", "--with", "pywin32", "--with", "playwright>=1.55,<2", "python", "$Payload\bench.py")
    for ($r = 0; $r -lt 3; $r++) {
      foreach ($o in "proc", "none", "canvas") {
        foreach ($g in "on", "off") {
          & uv @uvg tab $g 1600 900 60 "$work\chrome-win64" "$work\chrome-headless-shell-win64" $o 2>&1 | ForEach-Object { "$_" }
        }
      }
    }
    return
  }
  if ($Payload -and (Test-Path "$Payload\overlays.txt")) {
    # Overlay study: tab capture without a cursor, with a canvas compositor, and with insertable streams.
    foreach ($size in @(@(1600, 900), @(1280, 800), @(800, 450))) {
      foreach ($g in "on", "off") {
        foreach ($o in "none", "canvas", "proc") {
          & uv @uv tab $g $size[0] $size[1] 60 "$work\chrome-win64" "$work\chrome-headless-shell-win64" $o 2>&1 | ForEach-Object { "$_" }
        }
      }
    }
    return
  }
  $rounds = if ($env:ROUNDS) { [int]$env:ROUNDS } else { 2 }
  for ($r = 0; $r -lt $rounds; $r++) {
    foreach ($size in @(@(1600, 900), @(1280, 800), @(800, 450))) {
      foreach ($rate in 60, 30) {
        foreach ($g in "off", "on") {
          foreach ($path in "screencast", "tab") {
            & uv @uv $path $g $size[0] $size[1] $rate "$work\chrome-win64" "$work\chrome-headless-shell-win64" 2>&1 | ForEach-Object { "$_" }
          }
        }
      }
    }
  }
} finally {
  Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($work) } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep 1
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
  Write-Output ("CLEANED " + -not (Test-Path $work))
}
