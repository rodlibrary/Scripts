#!/usr/bin/env python3
"""
Diagnose Preservica login problems.

Shows where each credential value is coming from (credentials.properties or an
environment variable), flags common typos, then tries to log in and prints the
server's exact response. The password is never printed.

Run it from the folder that holds credentials.properties:
    python check_preservica_login.py
"""

import configparser
import os
import sys

import requests

PATH = "credentials.properties"
if not os.path.exists(PATH):   # not in the current folder: try next to this script
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), PATH)
    if os.path.exists(beside):
        PATH = beside
KEYS = {"username": "PRESERVICA_USERNAME", "password": "PRESERVICA_PASSWORD",
        "server": "PRESERVICA_SERVER", "tenant": "PRESERVICA_TENANT",
        "twoFactorToken": "PRESERVICA_2FA_TOKEN"}

if not os.path.exists(PATH):
    print(f"Can't find {PATH} in {os.getcwd()}")
    if os.path.exists(PATH + ".txt"):
        print(f"  Found {PATH}.txt instead. Rename it to remove the .txt.")
    sys.exit(1)

cfg = configparser.RawConfigParser(interpolation=None)
cfg.read(PATH, encoding="utf-8-sig")
section = next((x for x in cfg.sections() if x.strip().lower() == "credentials"), None)
if section is None:
    sys.exit(f"{PATH} has no [credentials] line at the top.")
if section != "credentials":
    print(f"Note: the first line is [{section}]. The scripts here accept that, but other tools need exactly [credentials].")

values, problems = {}, []
for key, env in KEYS.items():
    if os.environ.get(env) is not None:
        if not os.environ[env].strip():
            problems.append(f"environment variable {env} is set but empty: remove it (it hides the value in the file)")
        val, src = os.environ[env], f"environment variable {env} (overrides the file)"
    else:
        val, src = cfg[section].get(key), PATH
    values[key] = val
    if val is None:
        if key != "twoFactorToken":
            problems.append(f"{key} is missing")
        continue
    shown = "*" * min(len(val), 12) + f" ({len(val)} characters)" if key in ("password", "twoFactorToken") else val
    print(f"{key:15} {shown:40} from {src}")
    if val != val.strip():
        problems.append(f"{key} has spaces at the start or end")
    if val and (val[0] in "\"'" or val[-1] in "\"'"):
        problems.append(f"{key} is wrapped in quotes; remove them")
    if "REPLACE_WITH" in val:
        problems.append(f"{key} still has the placeholder text")

server = (values.get("server") or "").strip()
if server.startswith("http") or "/" in server:
    problems.append("server should be just the host name, e.g. us.preservica.com")
tenant = values.get("tenant") or ""
if tenant and tenant != tenant.upper():
    print("\nNote: tenant codes are usually all capitals. Check the case of yours.")

if problems:
    print("\nFix these first:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

data = {"username": values["username"], "password": values["password"], "tenant": tenant}
if values.get("twoFactorToken"):
    import pyotp  # installed with pyPreservica
    data["twoFactorToken"] = pyotp.TOTP(values["twoFactorToken"]).now()

url = f"https://{server}/api/accesstoken/login"
print(f"\nTrying to log in at {url} ...")
try:
    r = requests.post(url, data=data, timeout=30)
except requests.RequestException as exc:
    sys.exit(f"Couldn't reach the server: {exc}\nCheck the server name.")

if r.ok:
    print("Login succeeded. The export script should work with these credentials.")
else:
    body = r.text.replace(values["password"], "***")[:500]
    print(f"Login failed: HTTP {r.status_code}\nServer said: {body}")
    if r.status_code == 401:
        print("\nThe server is reachable, so the username, password or tenant was rejected.\n"
              "If you normally sign in through the university's login page, this account\n"
              "can't use a password; ask your Preservica administrator for a Preservica-only account.")
    elif r.status_code == 404:
        print("\nThat server doesn't have a Preservica login endpoint. Check the server name.")
