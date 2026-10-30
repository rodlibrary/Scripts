#!/usr/bin/env python3
"""
Find out why Preservica's search returns nothing for this account.

Tries the search several ways, plus a direct look-up of one folder through the
same search (Content) API, and prints what each returns. Nothing is changed.

    python preservica_search_test.py <folder-reference>

Uses the same credentials.properties as the other scripts. Requires: python -m pip install requests
"""

import json
import os
import sys

import requests

CREDENTIAL_KEYS = {"username": "PRESERVICA_USERNAME", "password": "PRESERVICA_PASSWORD",
                   "tenant": "PRESERVICA_TENANT", "server": "PRESERVICA_SERVER"}


def load_credentials(path="credentials.properties"):
    """
    Read the Preservica login from environment variables or credentials.properties.

    A non-empty PRESERVICA_* environment variable wins. Otherwise the value comes
    from credentials.properties, looked for first in the current folder and then
    next to this script. Exits with a plain explanation if anything is missing.
    """
    import configparser
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [path] if os.path.isabs(path) else [os.path.abspath(path), os.path.join(here, path)]
    found = next((p for p in candidates if os.path.isfile(p)), None)

    file_values = {}
    if found:
        cfg = configparser.RawConfigParser(interpolation=None)
        try:
            cfg.read(found, encoding="utf-8-sig")
        except (configparser.Error, UnicodeError) as exc:
            sys.exit(f"Couldn't read {found}: {exc}\nThe first line must be [credentials], "
                     "and the file should be saved as plain text (UTF-8).")
        section = next((s for s in cfg.sections() if s.strip().lower() == "credentials"), None)
        if section is None:
            sys.exit(f"{found} has no [credentials] line. Add it as the very first line.")
        file_values = {k.lower(): v.strip().strip("\"'") for k, v in cfg[section].items()}

    creds, missing = {}, []
    for key, env in CREDENTIAL_KEYS.items():
        value = (os.environ.get(env) or "").strip() or file_values.get(key, "")
        if not value or "REPLACE_WITH" in value:
            missing.append(key)
        creds[key] = value
    creds["two_fa"] = (os.environ.get("PRESERVICA_2FA_TOKEN") or "").strip() or file_values.get("twofactortoken") or None

    if missing:
        looked = "\n".join(f"  {p}" for p in dict.fromkeys(candidates))
        if found:
            problem = f"{found} has no value for: {', '.join(missing)}."
        else:
            problem = f"No credentials.properties found. Looked in:\n{looked}"
            for p in candidates:
                if os.path.isfile(p + ".txt"):
                    problem += f"\nFound {p}.txt instead: rename it to remove the .txt ending."
        sys.exit(f"{problem}\nThe file needs a [credentials] line followed by username=, password=, "
                 "server= and tenant= lines (see credentials.properties.example).")
    if creds["server"].startswith("http") or "/" in creds["server"]:
        sys.exit(f"server should be just the host name, e.g. us.preservica.com (it is {creds['server']!r}).")
    return creds


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python preservica_search_test.py <folder-reference>")
    folder = sys.argv[1].strip()
    c = load_credentials()
    base = f"https://{c['server']}"
    r = requests.post(f"{base}/api/accesstoken/login", timeout=60,
                      data={"username": c["username"], "password": c["password"], "tenant": c["tenant"]})
    if r.status_code != 200:
        sys.exit(f"Login failed (HTTP {r.status_code}): {r.text[:300]}")
    token = r.json()["token"]
    print(f"Logged in as {c['username']} (tenant {c['tenant']}).\n")
    h = {"Preservica-Access-Token": token}

    def show(label, resp):
        try:
            v = resp.json().get("value", {})
            hits = v.get("totalHits")
            extra = f"{hits} result(s)" if hits is not None else json.dumps(resp.json())[:200]
        except ValueError:
            extra = resp.text[:200].replace("\n", " ")
        print(f"{label:58} HTTP {resp.status_code}  {extra}")

    def search(label, q, metadata="xip.title"):
        show(label, requests.post(f"{base}/api/content/search", headers=h, timeout=60,
                                  data={"start": "0", "max": "5", "metadata": metadata, "q": json.dumps(q)}))

    print("Search requests (POST /api/content/search):")
    search('1. everything, query "%" (what the export used to send)', {"q": "%"})
    search('2. everything, query "*" (what the export sends now)', {"q": "*"})
    search('3. everything, empty query', {"q": ""})
    search('4. "*" with the export\'s reference field', {"q": "*", "fields": [{"name": "xip.reference", "values": []}]})
    search('5. "*" under the folder', {"q": "*", "fields": [{"name": "xip.parent_hierarchy", "values": [folder]}]})
    search('6. "*", assets only', {"q": "*", "fields": [{"name": "xip.document_type", "values": ["IO"]}]})

    print("\nOther checks:")
    show("7. GET /api/content/search?q=*", requests.get(f"{base}/api/content/search", headers=h, timeout=60,
         params={"q": json.dumps({"q": "*"}), "start": 0, "max": 5, "metadata": "xip.title"}))
    show("8. the folder through the search API (object-details)", requests.get(
         f"{base}/api/content/object-details", headers=h, timeout=60, params={"id": f"sdb:SO|{folder}"}))
    show("9. the folder through the Entity API", requests.get(
         f"{base}/api/entity/structural-objects/{folder}", headers=h, timeout=60))

    print("\nSend these lines to whoever is helping you. They contain no password or token.")


if __name__ == "__main__":
    main()
