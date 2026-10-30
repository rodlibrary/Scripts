#!/usr/bin/env python3
"""
Read, download and update Preservica descriptive metadata with plain REST calls.

Every command here makes the same requests that Preservica's Swagger pages
document, so you can compare a command's output with "Try it out" on:

    https://us.preservica.com/api/accesstoken/documentation.html   (logging in)
    https://us.preservica.com/api/entity/documentation.html        (records and metadata)

See API_GUIDE.md in this folder for a walkthrough.

Commands
    version                       Show the Preservica version (checks the login works)
    list     <reference>          List an asset's or folder's metadata fragments
    download <reference>          Save each metadata fragment as an .xml file
    update   <reference> --schema URI --file new.xml [--apply]
                                  Replace one fragment. Without --apply it only shows
                                  what it would send; with --apply it saves a backup first.

Add --folder when <reference> is a folder rather than an asset.
<reference> can be a bare reference, IO_/SO_<reference>, or a link copied from Preservica.

Credentials come from PRESERVICA_USERNAME / PRESERVICA_PASSWORD / PRESERVICA_TENANT /
PRESERVICA_SERVER, or from a credentials.properties file (the same one the export uses).

Requires:  python -m pip install requests
"""

import argparse
import configparser
import datetime as dt
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TOKEN_HEADER = "Preservica-Access-Token"


def local(tag):
    return tag.rsplit("}", 1)[-1]


def keep_default_namespace(elem):
    """Write the record's own namespace as the default (<mods xmlns=...>), not as ns0:."""
    if elem.tag.startswith("{"):
        ET.register_namespace("", elem.tag[1:].split("}", 1)[0])


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

class Preservica:
    """A minimal client: logs in, sends the token header, logs in again if the token expires."""

    def __init__(self, credentials_path):
        c = load_credentials(credentials_path)
        self.username, self.password = c["username"], c["password"]
        self.tenant, self.server = c["tenant"], c["server"]
        self.base = f"https://{self.server}"
        self.http = requests.Session()
        self.login()
        self._version = None

    def login(self):
        # POST /api/accesstoken/login  (form fields: username, password, tenant)
        r = self.http.post(f"{self.base}/api/accesstoken/login", timeout=60, data={
            "username": self.username, "password": self.password, "tenant": self.tenant})
        if r.status_code != 200:
            sys.exit(f"Login failed (HTTP {r.status_code}): {r.text[:300]}")
        self.http.headers[TOKEN_HEADER] = r.json()["token"]   # valid for 15 minutes

    def request(self, method, url, **kw):
        if url.startswith("/"):
            url = self.base + url
        r = self.http.request(method, url, timeout=120, **kw)
        if r.status_code == 401:          # token expired: log in again and retry once
            self.login()
            r = self.http.request(method, url, timeout=120, **kw)
        if r.status_code >= 400:
            sys.exit(f"{method} {url} failed (HTTP {r.status_code}): {r.text[:500]}")
        return r

    def version(self):
        # GET /api/entity/versiondetails/version
        if self._version is None:
            text = self.request("GET", "/api/entity/versiondetails/version").text
            m = re.search(r"<CurrentVersion>([\d.]+)</CurrentVersion>", text)
            self._version = m.group(1) if m else "unknown"
        return self._version

    def xip_namespace(self):
        major, minor = (self.version().split(".") + ["0"])[:2]
        return f"http://preservica.com/XIP/v{major}.{minor}"

    def entity(self, ref, folder=False):
        # GET /api/entity/information-objects/{ref}   (assets)
        # GET /api/entity/structural-objects/{ref}    (folders)
        kind = "structural-objects" if folder else "information-objects"
        root = ET.fromstring(self.request("GET", f"/api/entity/{kind}/{ref}").content)
        title = next((e.text for e in root.iter() if local(e.tag) == "Title"), "")
        fragments = []   # the response lists each fragment's URL and schema
        for meta in (e for e in root.iter() if local(e.tag) == "Metadata"):
            for frag in meta:
                if local(frag.tag) == "Fragment":
                    fragments.append({"schema": frag.get("schema"), "url": frag.text.strip()})
        return title, fragments

    def fragment(self, url):
        # GET /api/entity/{type}/{ref}/metadata/{metadataRef}
        # Returns a MetadataContainer; the record itself is inside <Content>.
        root = ET.fromstring(self.request("GET", url).content)
        content = next(e for e in root.iter() if local(e.tag) == "Content")
        keep_default_namespace(content[0])
        return ET.tostring(content[0], encoding="unicode")

    def update_fragment(self, url, entity_ref, schema, new_xml):
        # PUT /api/entity/{type}/{ref}/metadata/{metadataRef}
        body = build_container(self.xip_namespace(), url, entity_ref, schema, new_xml)
        return self.request("PUT", url, data=body.encode("utf-8"),
                            headers={"Content-Type": "application/xml;charset=UTF-8"})


def build_container(xip_ns, url, entity_ref, schema, new_xml):
    """The request body Preservica expects when replacing a fragment."""
    ET.register_namespace("xip", xip_ns)
    record = ET.fromstring(new_xml)
    keep_default_namespace(record)
    container = ET.Element(f"{{{xip_ns}}}MetadataContainer", {"schemaUri": schema})
    ET.SubElement(container, f"{{{xip_ns}}}Ref").text = url.rstrip("/").rsplit("/", 1)[-1]
    ET.SubElement(container, f"{{{xip_ns}}}Entity").text = entity_ref
    ET.SubElement(container, f"{{{xip_ns}}}Content").append(record)
    return ET.tostring(container, encoding="unicode", xml_declaration=False)


def clean_ref(value):
    m = UUID_RE.search(value)
    if not m:
        sys.exit(f"Not a Preservica reference: {value!r}")
    return m.group(0).lower()


def safe_name(schema):
    return re.sub(r"[^A-Za-z0-9]+", "_", schema).strip("_")[-60:] or "metadata"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["version", "list", "download", "update"])
    ap.add_argument("reference", nargs="?", help="Asset or folder reference")
    ap.add_argument("--folder", action="store_true", help="The reference is a folder, not an asset")
    ap.add_argument("--schema", help="Schema URI of the fragment (required for update)")
    ap.add_argument("--file", help="XML file with the new metadata (update)")
    ap.add_argument("--out", default="metadata_downloads", help="Folder for downloaded XML")
    ap.add_argument("--apply", action="store_true", help="Actually send the update")
    ap.add_argument("--credentials", default="credentials.properties")
    args = ap.parse_args()

    api = Preservica(args.credentials)
    if args.command == "version":
        print(f"Logged in to {api.server} as {api.username}. Preservica version {api.version()}.")
        return
    if not args.reference:
        ap.error(f"{args.command} needs a reference")
    ref = clean_ref(args.reference)
    title, fragments = api.entity(ref, args.folder)

    if args.command == "list":
        print(f"{title} ({ref}): {len(fragments)} metadata fragment(s)")
        for f in fragments:
            print(f"  {f['schema']}\n    {f['url']}")
        if not fragments:
            print("  None. If you expected some, the account may lack Read Metadata on this record's security tag.")

    elif args.command == "download":
        os.makedirs(args.out, exist_ok=True)
        for f in fragments:
            if args.schema and f["schema"] != args.schema:
                continue
            path = os.path.join(args.out, f"{ref}_{safe_name(f['schema'])}.xml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(api.fragment(f["url"]))
            print(f"Saved {path}")

    elif args.command == "update":
        if not (args.schema and args.file):
            ap.error("update needs --schema and --file")
        matches = [f for f in fragments if f["schema"] == args.schema]
        if not matches:
            sys.exit(f"{ref} has no fragment with schema {args.schema}. Run 'list' to see its schemas.")
        if len(matches) > 1:
            sys.exit(f"{ref} has {len(matches)} fragments with that schema; update one in Swagger instead.")
        with open(args.file, encoding="utf-8-sig") as fh:
            new_xml = fh.read()
        ET.fromstring(new_xml)  # stop now if the file isn't well-formed XML
        target = matches[0]["url"]
        if not args.apply:
            print(f"Dry run: would PUT to {target}\n")
            print(build_container(api.xip_namespace(), target, ref, args.schema, new_xml))
            print("\nRun again with --apply to send it.")
            return
        os.makedirs(args.out, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(args.out, f"{ref}_{safe_name(args.schema)}_backup_{stamp}.xml")
        with open(backup, "w", encoding="utf-8") as fh:
            fh.write(api.fragment(target))
        api.update_fragment(target, ref, args.schema, new_xml)
        print(f"Updated {target}\nPrevious version saved to {backup}")


if __name__ == "__main__":
    main()
