#!/usr/bin/env python3
import sys, json, urllib.request, urllib.parse, pathlib

BASE  = "https://nokia-fresh-optics-dedicated.trycloudflare.com"
TOKEN = "2XNo8N-NlzDcvqFfGeGi7Zzv"

def call(path, params=None, data=None, method=None):
    url = BASE + path + "?token=" + urllib.parse.quote(TOKEN)
    if params:
        url += "&" + urllib.parse.urlencode(params, doseq=True)
    body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "status":
    code, out = call("/status")
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "ls":
    code, out = call("/ls", {"path": sys.argv[2]}); print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "read":
    code, out = call("/read", {"path": sys.argv[2]})
    if code != 200: print("ERR", code, out); sys.exit(1)
    try:
        sys.stdout.write(json.loads(out)["text"])
    except Exception:
        print(out)

elif cmd == "write":
    local = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")
    code, out = call("/write", {"path": sys.argv[2]}, json.dumps({"content": local}), "POST")
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "edit":
    old = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")
    new = pathlib.Path(sys.argv[4]).read_text(encoding="utf-8")
    code, out = call("/edit", {"path": sys.argv[2]}, json.dumps({"old": old, "new": new}), "POST")
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "exec":
    code, out = call("/exec", None, json.dumps({"cmd": " ".join(sys.argv[2:])}), "POST")
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "result":
    code, out = call("/exec/result", {"id": sys.argv[2]})
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "approve":
    code, out = call("/approve", {"id": sys.argv[2], "action": "approve"})
    print(out); sys.exit(0 if code == 200 else 1)

elif cmd == "execfile":
    print("execute file contents via exec:", sys.argv[2])

else:
    print("usage: remote.py <status|ls|read|write|edit|exec> ...")
