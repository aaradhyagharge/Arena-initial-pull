# Arena Harness — setup (≈5 minutes)

What this is: a single-file, zero-install Python server that runs on **your** PC.
It lets the Arena agent (in this chat) read/write files and run commands on your
machine through a free, token-protected tunnel — and you can watch every step
live in your browser.

## Step 1 — get the file
Download `arena_harness.py` from this chat (workspace file) and save it
anywhere, e.g. `C:\tools\` or `~/tools/`.

## Step 2 — run it (keep the terminal open)
**Windows (PowerShell):**
```
py arena_harness.py --root "C:\Users\you\projects"
```
**macOS / Linux:**
```
python3 arena_harness.py --root ~/projects
```
The banner prints your **TOKEN** and your local dashboard URL.
`Ctrl+C` (or closing the terminal) is the kill switch.

## Step 3 — open a tunnel (one command, free, no account)
Install cloudflared (pick your OS):
- Windows: `winget install cloudflare.cloudflared`
- macOS: `brew install cloudflared`
- Linux: download from https://github.com/cloudflare/cloudflared/releases (or `apt install cloudflared`)

Then run:
```
cloudflared tunnel --url http://127.0.0.1:8787
```
It prints a line like:
```
https://random-words-1234.trycloudflare.com
```
(Alternative: `ngrok http 8787` — needs a free ngrok account.)

## Step 4 — hand me the keys
Paste both of these into the chat, plus the folder you want me to work in:
```
https://random-words-1234.trycloudflare.com
<TOKEN from step 2>
```
Then just tell me what to build/fix. I'll verify the connection, and we're live.

## Safety model (read this once)
- **Nothing is exposed** except that one tunnel URL. Stop cloudflared or
  `Ctrl+C` the harness → connection is gone instantly.
- **Token required** on every single call. Treat it like a password. Anyone
  with URL+token can do what I do — only share it in this chat.
- **File ops are confined** to the `--root` folder you pass. I cannot touch
  anything outside it.
- **Shell commands need your approval** (except obvious read-only ones like
  `git status`, `ls`, `cat`). They appear in your dashboard at
  `http://localhost:8787/?token=...` with approve/deny buttons — watch me work live.
- **Hard deny-list always on**: `rm -rf /`, `mkfs`, `dd` to raw disks,
  `shutdown/reboot`, Windows drive wipes (`rd /s /q c:\`, `format c:`), fork
  bombs, etc. — even in `--auto-exec` mode.
- **Audit log**: every operation is appended to `~/.arena_harness_audit.jsonl`
  (Windows: `C:\Users\you\.arena_harness_audit.jsonl`).
- **Permissive mode** (optional): add `--auto-exec` to skip approvals.
  The hard deny-list still applies. Only use it on a project you trust me with.

## What I can do once connected
- list/read/search files in your project
- create and modify files (full rewrite or surgical edits)
- run builds, tests, linters, git — with your approval per command
- iterate until the code actually works
- everything visible live in your browser dashboard, and logged

---

## Day 2 and beyond — every new chat (THIS is the important part)

**Every new Arena chat starts with a blank agent** — it has no memory of
previous chats, this harness, or your PC. Everything therefore lives OUTSIDE
the chat:

| Where | What it holds |
|---|---|
| The **paste block** (launcher prints it) | Fresh URL + token + full agent briefing + orders |
| **WORKLOG.md** in each project folder | That project's memory: decisions, progress, next steps |
| **C:\arena** (this folder) | The harness, the launcher, this guide |

### The 30-second ritual (same for every project, every chat)
1. Run `start-harness.ps1 "C:\path\to\the\project"` (or drag the folder).
2. Notepad pops open with **paste-block.txt** — select all, copy.
3. Paste it into the chat (new or old — does not matter), then write your task
   below the block, e.g. *"add dark mode to the settings page"*.
4. The agent pings `/status`, reads your WORKLOG.md, replies "link OK",
   and starts working. Your dashboard stays open to approve commands.
5. End of session: the agent updates WORKLOG.md. Close the launcher window —
   the token dies, the tunnel dies, the paste block stops working.

### A new project (= a new WORKLOG.md)
Nothing to configure: run the launcher with the new folder, paste the block.
The block's standing orders make the agent explore and **create** WORKLOG.md
on its first session, then maintain it forever after.

### Keep these files in C:\arena
- `arena_harness.py` — the server (v2 = IDE-style dashboard)
- `start-harness.ps1` — double-click launcher
- `SETUP.md` — this guide
- `paste-block.txt` — auto-generated each run (safe to delete; regenerated)

If the Arena workspace ever loses its copy of these files, your PC's copies
are the master — re-upload from C:\arena.
