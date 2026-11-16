#!/usr/bin/env python3
"""
check_resolve.py
================

Check resolve.csv, the list Apache uses to resolve ARKs, by the same rules
redirects.csv follows in github.com/rodlibrary/Redirects:

  * the header is ark,p_path;
  * every ark is a full address under our NAAN, such as
    https://library.uni.edu/ark:/32498/s3x7k9q2mb, and is listed once
    (hyphens and upper/lower case don't make a different ARK);
  * every p_path is a full https:// address on an allowed host
    (uni.access.preservica.com or digital.library.uni.edu), with characters
    such as | percent-encoded (sdb%3AIO%7C...), or 410 for a withdrawn item;
  * the file ends with a line break.

It names the line and the problem, and exits with status 1 if there are any.
generate_arks.py and match_preservica.py run the same checks before they
push, and won't push a resolve.csv that fails them.

    python3 check_resolve.py                    # resolve.csv in this folder
    python3 check_resolve.py other.csv
    export GITHUB_TOKEN=github_pat_...
    python3 check_resolve.py --from-github      # the copy in rodlibrary/Resolver

With --master-list it also lists minted ARKs that aren't in resolve.csv yet
(those wait for match_preservica.py) and ARKs in resolve.csv that were never
minted here.

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate_arks as ga  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Check resolve.csv (ark,p_path).")
    ap.add_argument("resolve", nargs="?", default=ga.DEFAULT_RESOLVE_MANIFEST,
                    help=f"the file to check (default: {ga.DEFAULT_RESOLVE_MANIFEST})")
    ap.add_argument("--master-list", help="also compare with this master list (ark_master_list.csv)")
    ap.add_argument("--from-github", action="store_true",
                    help="check the copy in the resolver repository (and its master list) instead")
    ap.add_argument("--naan", default=ga.DEFAULT_NAAN, help=f"our NAAN (default: {ga.DEFAULT_NAAN})")
    ap.add_argument("--ark-base", default=os.environ.get("ARK_BASE_URL") or ga.DEFAULT_ARK_BASE,
                    help=f"address ARKs resolve at (default: {ga.DEFAULT_ARK_BASE})")
    ap.add_argument("--allowed-host", action="append",
                    help="host a p_path may point to; repeat for several (default: "
                         + ", ".join(ga.DEFAULT_ALLOWED_TARGET_HOSTS) + ", or ALLOWED_TARGET_HOSTS)")
    ap.add_argument("--github-repo", default=os.environ.get("RESOLVER_REPO") or ga.DEFAULT_GITHUB_REPO,
                    help=f"resolver repository (default: {ga.DEFAULT_GITHUB_REPO})")
    ap.add_argument("--github-branch", default=os.environ.get("RESOLVER_BRANCH") or ga.DEFAULT_GITHUB_BRANCH,
                    help=f"its branch (default: {ga.DEFAULT_GITHUB_BRANCH})")
    ap.add_argument("--github-token-env", default=ga.DEFAULT_GITHUB_TOKEN_ENV,
                    help=f"environment variable holding a GitHub token (default: {ga.DEFAULT_GITHUB_TOKEN_ENV})")
    ap.add_argument("--github-api-url", default=ga.DEFAULT_GITHUB_API_URL, help=argparse.SUPPRESS)
    args = ap.parse_args()
    hosts = ga.allowed_hosts(args.allowed_host)

    master_text = None
    if args.from_github:
        token = os.environ.get(args.github_token_env, "").strip()
        try:
            raw = ga.fetch_file_from_github(args.github_api_url, args.github_repo,
                                            os.path.basename(args.resolve), args.github_branch, token)
            master_raw = ga.fetch_file_from_github(args.github_api_url, args.github_repo,
                                                   ga.DEFAULT_MASTER_LIST, args.github_branch, token)
        except RuntimeError as e:
            print(f"Couldn't download from GitHub: {e}")
            return 1
        if raw is None:
            print(f"{args.resolve} isn't in {args.github_repo}.")
            return 1
        text, source = raw.decode("utf-8-sig"), f"{args.github_repo}/{os.path.basename(args.resolve)}"
        master_text = master_raw.decode("utf-8-sig") if master_raw else None
    else:
        if not os.path.exists(args.resolve):
            print(f"{args.resolve} not found.")
            return 1
        with open(args.resolve, newline="", encoding="utf-8-sig") as f:
            text, source = f.read(), args.resolve
        if args.master_list:
            with open(args.master_list, newline="", encoding="utf-8-sig") as f:
                master_text = f.read()

    header = next(csv.reader(text.splitlines()), [])
    if [ga.normalize_header(h) for h in header] != list(ga.RESOLVE_FIELDS):
        print(f"{source}: the header should be ark,p_path; it is {','.join(header) or '(empty)'}")
        if "target_url" in [ga.normalize_header(h) for h in header]:
            print("  This is the older seven-column layout. generate_arks.py and match_preservica.py "
                  "convert it the next time they write the file.")
        return 1
    try:
        rows = ga.parse_resolve(text, args.ark_base, source)
    except ValueError as e:
        print(e)
        return 1
    errors = ga.check_resolve(rows, args.naan, args.ark_base, hosts, text=text)
    gone = sum(1 for r in rows if r["p_path"].strip().lower() in (ga.GONE, "gone"))
    print(f"{source}: {len(rows)} ARK(s) ({len(rows) - gone} redirect, {gone} withdrawn); {len(errors)} problem(s)")
    for e in errors[:200]:
        print(f"  ERROR {e}")
    if len(errors) > 200:
        print(f"  ... and {len(errors) - 200} more")

    if master_text is not None:
        minted = {ga.ark_key(m.get("ark", ""), args.naan): m.get("ark", "")
                  for m in csv.DictReader(master_text.splitlines())}
        minted.pop("", None)
        listed = {ga.ark_key(r["ark"], args.naan) for r in rows} - {""}
        waiting = [minted[k] for k in minted if k not in listed]
        never = [r["ark"] for r in rows if ga.ark_key(r["ark"], args.naan) not in minted]
        print(f"Master list: {len(minted)} ARK(s) minted; {len(waiting)} not in resolve.csv yet "
              f"(waiting for match_preservica.py); {len(never)} in resolve.csv but not in the master list")
        for a in never[:50]:
            print(f"  not minted here: {a}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
