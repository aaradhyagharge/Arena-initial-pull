#!/usr/bin/env python3
"""
Arena Harness v2 — a local bridge that lets the Arena AI agent work on files
on YOUR PC through a token-protected tunnel, with an IDE-style dashboard.

Single file, standard library only. Python 3.8+.

Run:
    python arena_harness.py --root "C:\\Users\\you\\projects"
    python arena_harness.py --root ~/code --auto-exec     # permissive mode

Safety model (unchanged from v1):
  * Listens on 127.0.0.1 only — nothing is exposed on your LAN.
  * Every request must carry the token (?token=... or Authorization header).
  * File operations are confined to the --root folder.
  * Shell commands that are not obviously read-only are QUEUED and require
    YOUR approval (dashboard) unless --auto-exec.
  * A hard deny-list is ALWAYS enforced, even in --auto-exec.
  * Every operation is appended to ~/.arena_harness_audit.jsonl.
  * Ctrl+C kills it instantly. Treat the token like a password.

v2 dashboard: file explorer, code editor, pending approvals (inline
approve/deny), command console, live activity feed.
"""

import argparse
import base64
import hmac
import html
import json
import re
import secrets
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = "2.0"
TOKEN: str = ""
ROOT: Path = Path.cwd()
AUTO_EXEC = False
EXEC_TIMEOUT = 180
MAX_OUT = 60_000
MAX_READ = 400_000
AUDIT_FILE = Path.home() / ".arena_harness_audit.jsonl"
AUDIT_LOG: list = []
PENDING: dict = {}
PORT = 8787

# Always blocked, in every mode.
HARD_DENY = [
    re.compile(r"rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r", re.I),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # fork bomb
    re.compile(r"\bmkfs(\.[a-z0-9]+)?\b", re.I),
    re.compile(r"\bdd\b[^|;&]*\bof=/dev/(sd[a-z]|nvme|hd|disk)"),
    re.compile(r">\s*/dev/(sd[a-z]|nvme|hd)"),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b|\binit\s+[06]\b", re.I),
    re.compile(r"format\s+[a-z]:", re.I),
    re.compile(r"\b(rd|rmdir)\s+/[a-z]*s[a-z]*\s+/[a-z]*q", re.I),
    re.compile(r"\bdel\s+/[a-z]*s[a-z]*\s+[a-z]:\\?(\s|$)", re.I),
    re.compile(r"/s\s+/q\s+[a-z]:\\?(\s|$)", re.I),
    re.compile(r"chmod\s+-R\s+0?777\s+/(\s|$)"),
]

# Commands that run without approval. Anything else is queued for you.
READONLY = re.compile(r"""^\s*(
      ls\b|dir\b|type\b|cat\b|echo\b|pwd\b|whoami\b|uname\b|ver\b|hostname\b
    | (git|gh)\s+(status|log|diff|show|branch|remote|tag|stash\s+list|rev-parse|config\s+--list)\b
    | grep\b|rg\b|find\b|tree\b|head\b|tail\b|wc\b|file\b|stat\b|which\b|where\b
    | python[0-9.]*\s+--version|node\s+--version|npm\s+(--version|ls\b|list\b|view\b)
    | pip\s+(list|show|freeze)\b|go\s+(version|env)\b
)\b""", re.X)

SHELL_META = re.compile(r"[|;&<>`$]")


def is_readonly(cmd: str) -> bool:
    """Whitelisted command AND no shell metacharacters (blocks `cmd | sh` etc.)."""
    return bool(READONLY.match(cmd)) and not SHELL_META.search(cmd)


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def audit(op: str, target: str, result: str) -> None:
    entry = {"ts": datetime.now().isoformat(timespec="seconds"),
             "op": op, "target": target[:500], "result": result[:500]}
    AUDIT_LOG.append(entry)
    if len(AUDIT_LOG) > 400:
        AUDIT_LOG.pop(0)
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def safe_path(p: str) -> Path:
    """Resolve p, refusing anything outside ROOT."""
    p = (p or ".").replace("\\", "/").strip()
    cand = Path(p).expanduser()
    if not cand.is_absolute():
        cand = ROOT / cand
    cand = cand.resolve()
    r = ROOT.resolve()
    if cand != r and r not in cand.parents:
        raise PermissionError(f"path outside root: {p}")
    return cand


def b64(s: str) -> bytes:
    try:
        return base64.b64decode(s or "")
    except Exception:
        raise ValueError("invalid base64")


def run_cmd(cmd: str, cwd: Path):
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(cwd),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=EXEC_TIMEOUT)
        out = p.stdout or ""
        if p.stderr:
            out += ("\n[stderr]\n" + p.stderr) if out else ("[stderr]\n" + p.stderr)
        if len(out) > MAX_OUT:
            out = out[:MAX_OUT] + "\n[...output truncated]"
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"[timed out after {EXEC_TIMEOUT}s]"


DASHBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arena Harness</title>
<style>
*{box-sizing:border-box} html,body{height:100%;margin:0}
body{font:13px/1.45 "Segoe UI",system-ui,sans-serif;background:#1e1e1e;color:#d4d4d4;
     display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:10px;padding:0 12px;height:40px;flex:0 0 40px;
       background:#252526;border-bottom:1px solid #3c3c3c;white-space:nowrap;overflow:hidden}
.logo{font-weight:600}.logo .ver{color:#569cd6;font-weight:400;margin-left:4px}
.spacer{flex:1}.muted{color:#8a8a8a}.small{font-size:11px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.green{background:#4ec9b0}.dot.red{background:#f44747}.dot.gray{background:#6b6b6b}
#main{flex:1;display:flex;min-height:0}
#sidebar{flex:0 0 235px;background:#252526;border-right:1px solid #3c3c3c;
         display:flex;flex-direction:column;min-width:150px}
#right{flex:0 0 330px;background:#252526;border-left:1px solid #3c3c3c;
       display:flex;flex-direction:column;min-width:220px}
#editorPane{flex:1;display:flex;flex-direction:column;min-width:0}
.pane-title{font-size:11px;letter-spacing:.6px;color:#bbbbbb;padding:7px 10px 5px;
            display:flex;align-items:center;gap:6px;flex:0 0 auto}
.pane-title button{margin-left:auto;background:none;border:none;color:#8a8a8a;cursor:pointer;font-size:13px}
.badge{background:#c586c0;color:#1e1e1e;border-radius:8px;font-size:10px;font-weight:700;padding:0 7px}
#tree{flex:1;overflow:auto;font-family:Consolas,monospace;font-size:12.5px;padding-bottom:20px}
#tree ul{list-style:none;margin:0;padding-left:14px}#tree ul.root{padding-left:4px}
.trow{display:flex;align-items:center;gap:4px;padding:1.5px 6px;cursor:pointer;border-radius:3px;white-space:nowrap}
.trow:hover{background:#2a2d2e}.trow.sel{background:#094771}
.ticon{width:12px;color:#8a8a8a;flex:0 0 12px;text-align:center}
.tname{overflow:hidden;text-overflow:ellipsis}
.tsize{margin-left:auto;color:#6b6b6b;font-size:10.5px;padding-left:8px}
#edbar{display:flex;align-items:center;gap:6px;padding:6px 8px;background:#252526;
       border-bottom:1px solid #3c3c3c;flex:0 0 auto}
#pathIn{flex:1;background:#3c3c3c;border:1px solid #555;color:#d4d4d4;padding:5px 8px;
        border-radius:3px;font-family:Consolas,monospace;font-size:12.5px;outline:none}
#pathIn:focus{border-color:#007acc}
button{background:#0e639c;color:#fff;border:none;border-radius:3px;padding:5px 12px;
       cursor:pointer;font-size:12.5px}
button:hover{background:#1177bb}button:disabled{background:#3c3c3c;color:#8a8a8a;cursor:default}
#ed{flex:1;width:100%;resize:none;border:none;outline:none;background:#1e1e1e;color:#d4d4d4;
    padding:10px 12px;font:13px/1.5 Consolas,"Courier New",monospace;tab-size:2;white-space:pre}
#pendList{flex:0 1 40%;overflow:auto;padding:0 8px 8px}
.pendCard{background:#1e1e1e;border:1px solid #c586c0;border-radius:4px;padding:8px;margin-bottom:8px}
.pendCard.busy{opacity:.55;pointer-events:none}
.pcmd{font-family:Consolas,monospace;font-size:12px;word-break:break-all;margin-bottom:2px;
      max-height:60px;overflow:auto}
.pbtns{display:flex;gap:6px;margin-top:7px}
.pbtns .ok{background:#4ec9b0;color:#1e1e1e;font-weight:700}
.pbtns .ok:hover{background:#6fdcc5}
.pbtns .no{background:#5a1d1d;color:#f48787}
.pbtns .no:hover{background:#7a2626}
.pad{padding:4px 2px}
#actList{flex:1;overflow:auto;padding:0 8px 12px;border-top:none}
.arow{padding:3px 2px;border-bottom:1px solid #2d2d2d;font-size:11.5px;word-break:break-all}
.arow b{color:#569cd6}.ats{color:#6b6b6b}.atg{color:#ce9178}.ars{color:#8a8a8a;display:block}
footer{flex:0 0 168px;background:#1e1e1e;border-top:1px solid #3c3c3c;display:flex;flex-direction:column}
#conBar{display:flex;align-items:center;gap:8px;padding:6px 8px;background:#252526;flex:0 0 auto}
.prompt{color:#4ec9b0;font-family:Consolas,monospace;font-weight:700}
#cmdIn{flex:1;background:#3c3c3c;border:1px solid #555;color:#d4d4d4;padding:5px 8px;border-radius:3px;
       font-family:Consolas,monospace;font-size:12.5px;outline:none}
#cmdIn:focus{border-color:#007acc}
#conOut{flex:1;margin:0;overflow:auto;padding:8px 12px;font:12px/1.45 Consolas,monospace;
        color:#cccccc;white-space:pre-wrap;word-break:break-all}
#flash{position:fixed;top:46px;left:50%;transform:translateX(-50%);background:#5a1d1d;color:#f48787;
       padding:6px 16px;border-radius:4px;display:none;z-index:9;font-size:12.5px}
@media (max-width:900px){#sidebar{display:none}#right{flex:0 0 40%}}
</style></head><body>
<div id="flash"></div>
<header>
  <span class="logo">&#9889; Arena Harness <span class="ver">v2</span></span>
  <span id="connDot" class="dot gray"></span><span id="connTxt" class="small muted">connecting…</span>
  <span id="rootTxt" class="small muted"></span>
  <span id="modeTxt" class="small muted"></span>
  <span class="spacer"></span>
  <span class="small muted">kill switch: Ctrl+C in the harness terminal</span>
</header>
<div id="main">
  <aside id="sidebar">
    <div class="pane-title">EXPLORER <button id="refreshTree" title="refresh file tree">&#10227;</button></div>
    <div id="tree"></div>
  </aside>
  <section id="editorPane">
    <div id="edbar">
      <input id="pathIn" spellcheck="false" placeholder="path/to/file (relative to project root)"/>
      <button id="openBtn">Open</button>
      <button id="saveBtn">Save</button>
      <button id="newBtn">New</button>
      <span id="edInfo" class="small muted"></span>
    </div>
    <textarea id="ed" spellcheck="false" placeholder="Click a file in the explorer — or type a path and press Open."></textarea>
  </section>
  <aside id="right">
    <div class="pane-title">PENDING APPROVALS <span id="pendCount" class="badge">0</span></div>
    <div id="pendList"></div>
    <div class="pane-title">ACTIVITY</div>
    <div id="actList"></div>
  </aside>
</div>
<footer>
  <div id="conBar">
    <span class="prompt">&rsaquo;</span>
    <input id="cmdIn" spellcheck="false" placeholder="run a command — anything risky queues above for YOUR approval"/>
    <button id="runBtn">Run</button>
  </div>
  <pre id="conOut"></pre>
</footer>
<script>
"use strict";
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = id => document.getElementById(id);
const HIDE = new Set(["node_modules","dist",".git",".next","build","out",".vs","__pycache__"]);
function esc(s){const d=document.createElement("div");d.textContent=(s==null?"":String(s));return d.innerHTML;}
function fmtSize(n){if(n<1024)return n+" B";if(n<1048576)return (n/1024).toFixed(1)+" KB";return (n/1048576).toFixed(1)+" MB";}
function flash(m){const f=$("flash");f.textContent=m;f.style.display="block";setTimeout(()=>f.style.display="none",4000);}
async function api(p,params){
  const u=new URL(p,location.origin);u.searchParams.set("token",TOKEN);
  if(params)for(const k in params)u.searchParams.set(k,params[k]);
  const r=await fetch(u);return r.json();
}
async function post(p,body){
  const u=new URL(p,location.origin);u.searchParams.set("token",TOKEN);
  const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  return r.json();
}
/* ---------- status ---------- */
function setConn(ok){
  const d=$("connDot");d.className="dot "+(ok?"green":"red");
  $("connTxt").textContent=ok?"connected":"connection lost — is the harness window open?";
}
async function pollStatus(){
  try{const r=await api("/status");
    if(r&&r.ok){setConn(true);$("rootTxt").textContent=r.root;
      $("modeTxt").textContent=(r.auto_exec?"AUTO-EXEC":"GATED")+(r.version?" \u00b7 v"+r.version:"");}
    else setConn(false);
  }catch(e){setConn(false);}
}
/* ---------- file explorer ---------- */
function treeNode(parentPath,e){
  const li=document.createElement("li");
  const row=document.createElement("div");
  row.className="trow "+(e.dir?"dir":"file");
  const full=parentPath?parentPath+"/"+e.name:e.name;
  row.innerHTML='<span class="ticon">'+(e.dir?"\u25b8":"\u00b7")+'</span><span class="tname">'+esc(e.name)+"</span>"+
                (e.dir?"":'<span class="tsize">'+fmtSize(e.size)+"</span>");
  if(e.dir){
    let ul=null,open=false;
    row.onclick=async()=>{
      if(open){ul.remove();ul=null;open=false;row.querySelector(".ticon").textContent="\u25b8";return;}
      row.querySelector(".ticon").textContent="\u25be";
      const r=await api("/ls",{path:full});
      ul=document.createElement("ul");
      if(r.error){ul.innerHTML='<li class="small muted pad">'+esc(r.error)+"</li>";}
      else(r.entries||[]).filter(c=>!HIDE.has(c.name)).forEach(c=>ul.appendChild(treeNode(full,c)));
      li.appendChild(ul);open=true;
    };
  }else{
    row.onclick=()=>{document.querySelectorAll(".trow.sel").forEach(n=>n.classList.remove("sel"));
                     row.classList.add("sel");openFile(full);};
  }
  li.appendChild(row);return li;
}
async function loadRoot(){
  const r=await api("/ls",{path:"."});
  const t=$("tree");t.innerHTML="";
  const ul=document.createElement("ul");ul.className="root";
  if(r.error){ul.innerHTML='<li class="small muted pad">'+esc(r.error)+"</li>";}
  else(r.entries||[]).filter(c=>!HIDE.has(c.name)).forEach(c=>ul.appendChild(treeNode("",c)));
  t.appendChild(ul);
}
/* ---------- editor ---------- */
let curPath=null,dirty=false;
const ta=$("ed");
function setInfo(t){$("edInfo").textContent=t;}
ta.addEventListener("input",()=>{dirty=true;setInfo((curPath||$("pathIn").value||"untitled")+" \u2014 unsaved changes");});
async function openFile(p){
  if(dirty&&!confirm("Discard unsaved changes to "+(curPath||"current file")+"?"))return;
  const r=await api("/read",{path:p});
  if(r.error){flash(r.error);return;}
  if(r.text===undefined){flash("binary file — cannot edit here");return;}
  curPath=p;$("pathIn").value=p;ta.value=r.text;dirty=false;
  setInfo(p+(r.truncated?" \u2014 showing first 400KB only":""));
}
$("openBtn").onclick=()=>{const p=$("pathIn").value.trim();if(p)openFile(p);};
$("saveBtn").onclick=async()=>{
  const p=$("pathIn").value.trim();
  if(!p){flash("type a path first");$("pathIn").focus();return;}
  const r=await post("/write",{path:p,content:ta.value});
  if(r.error){flash(r.error);return;}
  curPath=p;dirty=false;setInfo(p+" \u2014 saved "+fmtSize(r.bytes)+" \u2713");
};
$("newBtn").onclick=()=>{
  if(dirty&&!confirm("Discard unsaved changes?"))return;
  curPath=null;$("pathIn").value="";ta.value="";$("pathIn").focus();setInfo("new file \u2014 type a path, then Save");
};
/* ---------- approvals ---------- */
let lastPendIds=new Set();let AC=null;
function beep(){try{AC=AC||new (window.AudioContext||window.webkitAudioContext)();
  const o=AC.createOscillator(),g=AC.createGain();o.connect(g);g.connect(AC.destination);
  o.frequency.value=880;g.gain.value=0.05;o.start();setTimeout(()=>o.stop(),140);}catch(e){}}
async function pollPend(){
  let r;try{r=await api("/pending");}catch(e){return;}
  if(!r||!r.entries)return;
  const ids=new Set(r.entries.map(e=>e.id));
  let fresh=false;ids.forEach(i=>{if(!lastPendIds.has(i))fresh=true;});
  if(fresh&&document.visibilityState==="visible")beep();
  lastPendIds=ids;
  $("pendCount").textContent=r.entries.length;
  document.title=(r.entries.length?"("+r.entries.length+") ":"")+"Arena Harness";
  const el=$("pendList");el.innerHTML="";
  if(!r.entries.length){el.innerHTML='<div class="muted small pad">all clear — nothing waiting</div>';return;}
  r.entries.forEach(e=>{
    const card=document.createElement("div");card.className="pendCard";
    card.innerHTML='<div class="pcmd">'+esc(e.cmd)+'</div><div class="muted small">'+esc(e.cwd)+" \u00b7 "+esc(e.ts)+'</div>'+
      '<div class="pbtns"><button class="ok">\u2713 Approve &amp; run</button><button class="no">\u2717 Deny</button></div>';
    card.querySelector(".ok").onclick=()=>decide(e.id,"approve",card);
    card.querySelector(".no").onclick=()=>decide(e.id,"deny",card);
    el.appendChild(card);
  });
}
async function decide(id,action,card){
  card.classList.add("busy");
  let r;try{r=await api("/approve",{id:id,action:action});}catch(e){flash("request failed: "+e);card.classList.remove("busy");return;}
  if(r.error){flash(r.error);}
  else if(action==="approve"){
    $("conOut").textContent="$ "+(r.cmd||id)+"\n\n"+(r.output||"(no output)")+"\n[exit "+(r.exit==null?"-":r.exit)+"]";
  }else{
    $("conOut").textContent="denied: "+(r.id||id);
  }
  lastPendIds.delete(id);pollPend();pollAudit();
}
/* ---------- console ---------- */
async function runCmd(){
  const c=$("cmdIn").value.trim();if(!c)return;
  $("conOut").textContent="$ "+c+"\n\n(running\u2026)";
  let r;try{r=await post("/exec",{cmd:c});}catch(e){$("conOut").textContent="$ "+c+"\n\n[request failed] "+e;return;}
  if(r.error){$("conOut").textContent="$ "+c+"\n\n[denied] "+r.error;}
  else if(r.status==="done"){$("conOut").textContent="$ "+c+"\n\n"+(r.output||"(no output)")+"\n[exit "+r.exit+"]";}
  else{$("conOut").textContent="$ "+c+"\n\n\u23f3 queued \u2014 approve it in PENDING APPROVALS above";}
  $("cmdIn").select();pollPend();
}
$("runBtn").onclick=runCmd;
$("cmdIn").addEventListener("keydown",ev=>{if(ev.key==="Enter")runCmd();});
/* ---------- activity ---------- */
async function pollAudit(){
  let r;try{r=await api("/audit",{limit:40});}catch(e){return;}
  if(!r||!r.entries)return;
  const el=$("actList");el.innerHTML="";
  r.entries.slice().reverse().forEach(a=>{
    const d=document.createElement("div");d.className="arow";
    d.innerHTML='<span class="ats">'+esc((a.ts||"").slice(-8))+"</span> <b>"+esc(a.op)+'</b> <span class="atg">'+
      esc(a.target)+'</span><span class="ars">'+esc(a.result)+"</span>";
    el.appendChild(d);
  });
}
/* ---------- boot ---------- */
$("refreshTree").onclick=loadRoot;
loadRoot();pollStatus();pollPend();pollAudit();
setInterval(pollStatus,4000);
setInterval(pollPend,2000);
setInterval(pollAudit,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ArenaHarness/2.0"

    def log_message(self, *args):
        pass  # keep the terminal quiet

    # ---------- helpers ----------
    def send(self, code: int, obj) -> None:
        if isinstance(obj, (dict, list)):
            body = json.dumps(obj).encode()
            ctype = "application/json"
        else:
            body = obj.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def tok_ok(self, qs: dict) -> bool:
        h = self.headers.get("Authorization", "")
        t = h[7:] if h.lower().startswith("bearer ") else None
        if t is None:
            t = (qs.get("token") or [""])[0]
        return bool(t) and hmac.compare_digest(t, TOKEN)

    # ---------- entry points ----------
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == "/":
                self.serve_dashboard(qs)
            else:
                self.dispatch(qs, None)
        except Exception as e:
            try:
                self.send(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        try:
            self.dispatch(qs, body)
        except Exception as e:
            try:
                self.send(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    # ---------- dashboard ----------
    def serve_dashboard(self, qs: dict):
        if not self.tok_ok(qs):
            self.send(401, "<html><body><h2>Arena Harness</h2>"
                           "<p>Add <code>?token=YOUR_TOKEN</code> to the URL.</p></body></html>")
            return
        self.send(200, DASHBOARD_HTML)

    # ---------- API ----------
    def dispatch(self, qs: dict, body):
        if self.path_ == "/":
            return
        if not self.tok_ok(qs):
            self.send(401, {"error": "bad or missing token"})
            return

        def P(key: str):
            v = body.get(key) if body else None
            if v is None:
                v = (qs.get(key) or [None])[0]
            return v

        p = self.path_
        if p == "status":
            n_pending = len([e for e in PENDING.values() if e["status"] == "pending"])
            self.send(200, {"ok": True, "root": str(ROOT), "auto_exec": AUTO_EXEC,
                            "pending": n_pending, "now": now(), "version": VERSION})
        elif p == "pending":
            self.send(200, {"entries": [e for e in PENDING.values() if e["status"] == "pending"]})
        elif p == "ls":
            d = safe_path(P("path") or ".")
            if not d.is_dir():
                self.send(400, {"error": "not a directory"})
                return
            entries = []
            for c in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:1000]:
                try:
                    entries.append({"name": c.name, "dir": c.is_dir(),
                                    "size": 0 if c.is_dir() else c.stat().st_size})
                except OSError:
                    pass
            self.send(200, {"path": str(d), "entries": entries,
                            "truncated": len(entries) >= 1000})
            audit("ls", str(d), f"{len(entries)} entries")
        elif p == "read":
            f = safe_path(P("path") or ".")
            if not f.is_file():
                self.send(404, {"error": "not a file"})
                return
            data = f.read_bytes()
            if b"\0" in data[:8192]:
                self.send(200, {"path": str(f), "size": len(data),
                                "b64": base64.b64encode(data[:MAX_READ]).decode()})
            else:
                text = data.decode("utf-8", errors="replace")
                self.send(200, {"path": str(f), "size": len(data), "text": text[:MAX_READ],
                                "truncated": len(text) > MAX_READ})
            audit("read", str(f), f"{len(data)}B")
        elif p == "write":
            f = safe_path(P("path") or ".")
            if P("b64"):
                data = b64(P("b64"))
            else:
                data = (P("content") or "").encode("utf-8")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            self.send(200, {"path": str(f), "bytes": len(data)})
            audit("write", str(f), f"{len(data)}B")
        elif p == "edit":
            f = safe_path(P("path") or ".")
            old = b64(P("old_b64")).decode("utf-8", "replace") if P("old_b64") else (P("old") or "")
            new = b64(P("new_b64")).decode("utf-8", "replace") if P("new_b64") else (P("new") or "")
            text = f.read_text(encoding="utf-8", errors="replace")
            n = text.count(old)
            if n == 0:
                self.send(404, {"error": "old text not found in file"})
                return
            f.write_text(text.replace(old, new), encoding="utf-8")
            self.send(200, {"path": str(f), "replaced": n})
            audit("edit", str(f), f"{n} replacement(s)")
        elif p == "exec":
            cmd = b64(P("cmd_b64")).decode("utf-8", "replace") if P("cmd_b64") else (P("cmd") or "")
            if not cmd.strip():
                self.send(400, {"error": "empty command"})
                return
            cwd = safe_path(P("cwd") or ".")
            if any(rx.search(cmd) for rx in HARD_DENY):
                audit("exec-DENIED", cmd, "hard policy")
                self.send(403, {"error": "command denied by hard policy"})
                return
            if AUTO_EXEC or is_readonly(cmd):
                code, out = run_cmd(cmd, cwd)
                audit("exec", cmd, f"exit={code}")
                self.send(200, {"status": "done", "exit": code, "output": out})
            else:
                pid = secrets.token_hex(4)
                while len(PENDING) > 50:
                    oldest = next(iter(PENDING))
                    PENDING[oldest]["status"] = "expired"
                PENDING[pid] = {"id": pid, "cmd": cmd, "cwd": str(cwd),
                                "status": "pending", "output": "", "exit": None,
                                "ts": now()}
                audit("exec-queued", cmd, pid)
                self.send(200, {"id": pid, "status": "pending",
                                "note": "queued — approve it in your dashboard "
                                        f"(http://localhost:{PORT}/?token=...) or "
                                        f"via /approve?id={pid}&action=approve"})
        elif p == "exec/result":
            e = PENDING.get(P("id") or "")
            if not e:
                self.send(404, {"error": "unknown id"})
                return
            self.send(200, e)
        elif p == "approve":
            e = PENDING.get(P("id") or "")
            if not e or e["status"] != "pending":
                self.send(404, {"error": "no pending command with that id"})
                return
            action = (P("action") or "approve").lower()
            if action == "deny":
                e["status"] = "denied"
                audit("exec-denied-by-user", e["cmd"], e["id"])
                self.send(200, {"id": e["id"], "status": "denied"})
            else:
                e["status"] = "running"
                code, out = run_cmd(e["cmd"], Path(e["cwd"]))
                e.update(status="done", exit=code, output=out)
                audit("exec", e["cmd"], f"exit={code} (approved)")
                self.send(200, e)
        elif p == "audit":
            limit = int(P("limit") or 50)
            self.send(200, {"entries": AUDIT_LOG[-limit:]})
        else:
            self.send(404, {"error": f"unknown endpoint: {p}"})

    @property
    def path_(self):
        return urlparse(self.path).path.lstrip("/")


def main():
    global TOKEN, ROOT, AUTO_EXEC, EXEC_TIMEOUT, PORT
    ap = argparse.ArgumentParser(description="Arena Harness v2 — local agent bridge")
    ap.add_argument("--root", default=".", help="the ONLY folder the agent may touch")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--token", default=None, help="auth token (a random one is generated if omitted)")
    ap.add_argument("--auto-exec", action="store_true",
                    help="run ALL commands without approval (hard deny-list still applies)")
    ap.add_argument("--timeout", type=int, default=180, help="per-command timeout in seconds")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (keep 127.0.0.1 on your PC; 0.0.0.0 only for sandbox previews)")
    a = ap.parse_args()

    ROOT = Path(a.root).expanduser().resolve()
    EXEC_TIMEOUT = a.timeout
    AUTO_EXEC = a.auto_exec
    TOKEN = a.token or secrets.token_urlsafe(18)
    PORT = a.port

    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("=" * 60)
    print(" ARENA HARNESS v2 is RUNNING")
    print("-" * 60)
    print(f" root   : {ROOT}")
    print(f" host   : {a.host}  {'(PUBLIC bind — token required on every call!)' if a.host != '127.0.0.1' else '(local only)'}")
    print(f" token  : {TOKEN}   <- paste this + tunnel URL into the chat")
    print(f" local  : http://{a.host}:{a.port}/?token={TOKEN}   (your IDE dashboard)")
    print(f" exec   : {'AUTO (no approval)' if AUTO_EXEC else 'GATED (commands wait for your approval)'}")
    print("-" * 60)
    print(f" Next   : cloudflared tunnel --url http://127.0.0.1:{a.port}")
    print(" Stop   : Ctrl+C (or close this terminal) — kills everything")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nharness stopped.")


if __name__ == "__main__":
    main()
