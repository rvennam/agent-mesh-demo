#!/usr/bin/env python3
"""jwt-decode.py [token|file|-]: pretty-print a JWT's claims (no verification; display only)."""
import base64, json, sys, os
arg = sys.argv[1] if len(sys.argv) > 1 else "-"
tok = sys.stdin.read() if arg == "-" else (open(arg).read() if os.path.exists(arg) else arg)
tok = tok.strip().split()[-1]
def b64(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
h, p = tok.split(".")[:2]
print(json.dumps({"header": json.loads(b64(h)), "claims": json.loads(b64(p))}, indent=2))
