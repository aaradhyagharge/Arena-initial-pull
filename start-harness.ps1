# start-harness.ps1 - one launcher: starts the Arena Harness + a Cloudflare tunnel,
# then prints (and saves) the full AGENT BRIEFING block to paste into ANY new chat.
# Ctrl+C (or closing this window) stops everything.
#
# Usage (PowerShell):
#   .\start-harness.ps1 "C:\path\to\your\project"
# or run it from inside the project folder with no argument.

param([string]$Root = (Get-Location).Path)

$port  = 8787
$token = [guid]::NewGuid().ToString("N")
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path "$here\arena_harness.py")) {
    Write-Host "arena_harness.py not found next to this launcher ($here)." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found. Install it first:  winget install cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}
$pyExe = "py"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $pyExe = "python" }

Write-Host "starting harness (root: $Root, port: $port)..."
$hp = Start-Process -FilePath $pyExe -PassThru `
    -ArgumentList "`"$here\arena_harness.py`" --root `"$Root`" --port $port --token $token"

$log  = Join-Path $env:TEMP "arena-harness-tunnel.log"
$err  = Join-Path $env:TEMP "arena-harness-tunnel.err.log"
Remove-Item $log, $err -ErrorAction SilentlyContinue
$cp = Start-Process -FilePath cloudflared -PassThru -WindowStyle Hidden `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$port" `
    -RedirectStandardOutput $log -RedirectStandardError $err

try {
    $url = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        foreach ($f in @($log, $err)) {
            if (Test-Path $f) {
                $m = Select-String -Path $f -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -List
                if ($m) { $url = $m.Matches[0].Value; break }
            }
        }
        if ($url) { break }
    }
    if (-not $url) {
        Write-Host "tunnel did not come up in 30s - check $err" -ForegroundColor Yellow
        exit 1
    }

    $block = @"
=============================================================
 ARENA HARNESS is LIVE.
 Your dashboard (watch + approve):  http://127.0.0.1:$port/?token=$token
 Copy EVERYTHING between the marker lines into any NEW Arena chat.
=============================================================
----- BEGIN AGENT BRIEFING (copy from this line) -----
You are an Arena agent. I run "Arena Harness" on my Windows PC: a token-gated
bridge that lets you read/write files and run commands inside ONE project
folder. Drive it over HTTPS from your bash tool (curl or python urllib).

TUNNEL : $url
TOKEN  : $token
ROOT   : $Root

API - token goes in ?token= on EVERY call. GET unless noted:
  /status                          health check
  /ls?path=<rel>                   list dir  -> {entries:[{name,dir,size}]}
  /read?path=<rel>                 read file -> {path,size,text,truncated}
  POST /write  {"path":..,"content":..}      write whole file (UTF-8)
  POST /edit   {"path":..,"old":..,"new":..} replace text in file
  POST /exec   {"cmd":..}                    run a command (cmd.exe)
  /pending                         commands waiting for my approval
  /exec/result?id=<id>             result of a queued/finished command
  /audit?limit=50                  recent operations log

HARNESS RULES:
- Paths are relative to ROOT and cannot leave it. Quote paths with spaces.
- Obviously read-only commands run at once. EVERYTHING ELSE queues and waits
  for me to click Approve in my dashboard. When a command queues: tell me it
  is waiting, then poll /exec/result?id=<id>. Never spam retries; wait for me.
- A hard deny-list blocks destructive commands no matter what.
- POST /write REQUIRES a JSON body containing "content". A raw-text body
  silently writes a 0-byte file. After writing anything important, read it
  back and verify.
- Batch work into as few HTTP calls as you can; each call crosses a tunnel.

STANDING ORDERS:
1. First: GET /status, confirm the link, then read WORKLOG.md from the project
   root if it exists - it is your project memory. If missing: explore the
   project, then create WORKLOG.md (project, stack, decisions, progress, next).
2. Then do the task I give you. Verify your work (parse/build/lint) before
   declaring it done.
3. Last: update WORKLOG.md with what changed and what is next.
4. Reply "link OK" plus a one-line summary of WORKLOG.md once connected.
----- END AGENT BRIEFING -----
Paste YOUR TASK right after the block, e.g.: "add dark mode to settings page".
=============================================================
 Kill switch: Ctrl+C in THIS window (or close it) kills harness + tunnel.
=============================================================
"@

    Write-Host $block
    $outFile = Join-Path $here "paste-block.txt"
    $block | Out-File -FilePath $outFile -Encoding ascii
    Write-Host ""
    Write-Host "(also saved to $outFile - opening it in Notepad for easy copying)"
    Start-Process notepad.exe -ArgumentList "`"$outFile`""

    while (-not $cp.HasExited -and -not $hp.HasExited) { Start-Sleep -Seconds 2 }
}
finally {
    Write-Host ""
    Write-Host "stopping harness and tunnel..."
    foreach ($p in @($hp, $cp)) {
        if ($p -and -not $p.HasExited) { try { $p.Kill() } catch {} }
    }
    Remove-Item $log, $err -ErrorAction SilentlyContinue
}
