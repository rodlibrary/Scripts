#!/usr/bin/env python3
"""
check_github.py
===============

Test the GitHub connection the ARK scripts use, step by step, without changing
resolve.csv or anything else on the main branch.

    export GITHUB_TOKEN=github_pat_...     (PowerShell: $env:GITHUB_TOKEN = "github_pat_...")
    python3 check_github.py                # every check, including a test push
    python3 check_github.py --no-write     # read-only checks

It checks, in order, and stops at the first problem with an explanation:

  1. GitHub's API can be reached from this computer;
  2. the token is set and accepted;
  3. the token can see the repository (default rodlibrary/Resolver);
  4. the branch (default main) exists;
  5. resolve.csv and ark_master_list.csv can be read from it (and resolve.csv
     has the header ark,p_path);
  6. a push works: it creates a temporary branch, pushes a small test file to
     it the same way the ARK scripts push resolve.csv, and deletes the branch
     again. The main branch is never written to.

Standard library only. The token is never printed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate_arks as ga  # noqa: E402

if not hasattr(ga, "fetch_file_from_github"):
    sys.exit(f"The generate_arks.py next to this script ({ga.__file__}) is an older version. "
             "Download the current one from https://github.com/rodlibrary/Scripts/tree/main/arks.")

TOKEN_FIX = ("Create a fine-grained personal access token at github.com > Settings > Developer settings > "
             "Personal access tokens > Fine-grained tokens: resource owner {owner}, repository access "
             "'Only select repositories' > {repo}, permission Contents: Read and write.")


def call(method: str, url: str, token: str = "", body: "dict | None" = None, timeout: int = 30):
    """(HTTP status, parsed JSON or text). Status 0 means the server couldn't be reached."""
    headers = {"User-Agent": "check_github.py", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(getattr(e, "reason", e))
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def message(body) -> str:
    return str(body.get("message") if isinstance(body, dict) else body)[:200]


def main() -> int:
    ga.reject_gitlab_options()
    ap = argparse.ArgumentParser(description="Test the GitHub connection used by the ARK scripts.")
    ap.add_argument("--github-repo", default=os.environ.get("RESOLVER_REPO") or ga.DEFAULT_GITHUB_REPO)
    ap.add_argument("--github-branch", default=os.environ.get("RESOLVER_BRANCH") or ga.DEFAULT_GITHUB_BRANCH)
    ap.add_argument("--github-token-env", default=ga.DEFAULT_GITHUB_TOKEN_ENV)
    ap.add_argument("--github-api-url", default=ga.DEFAULT_GITHUB_API_URL, help=argparse.SUPPRESS)
    ap.add_argument("--no-write", action="store_true", help="skip the test push")
    args = ap.parse_args()

    api = args.github_api_url.rstrip("/")
    repo = args.github_repo.strip("/")
    owner = repo.split("/")[0]
    branch = args.github_branch
    token = os.environ.get(args.github_token_env, "").strip()
    fix = TOKEN_FIX.format(owner=owner, repo=repo)
    step = [0]

    def ok(msg):
        step[0] += 1
        print(f"  OK   {step[0]}. {msg}")

    def fail(msg, how):
        step[0] += 1
        print(f"  FAIL {step[0]}. {msg}\n         {how}")
        return 1

    print(f"Checking https://github.com/{repo} (branch {branch})")

    # 1. Reachable
    status, body = call("GET", f"{api}/zen")
    if status == 0:
        return fail(f"Can't reach {api}: {body}",
                    "This computer can't connect to GitHub. Check the network connection or proxy.")
    ok(f"{api} can be reached")

    # 2 and 3. Token and repository
    if not token:
        return fail(f"{args.github_token_env} isn't set",
                    f'Set it first: PowerShell  $env:{args.github_token_env} = "github_pat_..."   '
                    f"Mac/Linux  export {args.github_token_env}=github_pat_...")
    status, body = call("GET", f"{api}/repos/{repo}", token)
    if status == 401:
        return fail("GitHub rejected the token (HTTP 401)",
                    "The token is wrong, expired or revoked. " + fix
                    + " Update the RESOLVER_TOKEN Actions secret too.")
    ok("the token is accepted")
    if status in (403, 404):
        return fail(f"The token can't see {repo} (HTTP {status}: {message(body)})",
                    f"Check the name in its address (https://github.com/{repo}). " + fix
                    + f" If {owner} requires approval for fine-grained tokens, an owner must approve it first.")
    if status != 200:
        return fail(f"Reading {repo} failed (HTTP {status}): {message(body)}", "See the message above.")
    default_branch = body.get("default_branch") if isinstance(body, dict) else None
    visibility = body.get("visibility") if isinstance(body, dict) else None
    ok(f"the token can see {body.get('full_name', repo)}"
       + (f" ({visibility}, default branch {default_branch})" if default_branch else ""))

    # 4. Branch
    status, body = call("GET", f"{api}/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}", token)
    if status == 404:
        return fail(f"There's no branch {branch!r}",
                    f"The repository's default branch is {default_branch!r}: use --github-branch {default_branch}"
                    if default_branch else "Check the branch name.")
    if status != 200:
        return fail(f"Reading branch {branch} failed (HTTP {status}): {message(body)}", "See the message above.")
    head_sha = ((body.get("commit") or {}).get("sha")) if isinstance(body, dict) else None
    protected = isinstance(body, dict) and body.get("protected")
    ok(f"branch {branch} exists" + (" and is protected" if protected else ""))
    if protected:
        step[0] += 1
        print(f"  NOTE {step[0]}. {branch} has branch protection. If it requires pull requests, the scripts' "
              "direct pushes will be refused unless the token's account may bypass the rule.")

    # 5. Files
    missing = []
    for path in (ga.DEFAULT_RESOLVE_MANIFEST, ga.DEFAULT_MASTER_LIST):
        try:
            data = ga.fetch_file_from_github(api, repo, path, branch, token)
        except RuntimeError as e:
            return fail(f"Reading {path} failed: {e}", "See the message above.")
        if data is None:
            missing.append(path)
        else:
            text = data.decode("utf-8-sig", "replace")
            rows = max(0, len(text.strip().splitlines()) - 1)
            ok(f"{path} can be read ({rows} row(s))")
            header = [h.strip().lower() for h in (text.splitlines() or [""])[0].split(",")]
            if path == ga.DEFAULT_RESOLVE_MANIFEST and rows and header != list(ga.RESOLVE_FIELDS):
                step[0] += 1
                print(f"  NOTE {step[0]}. {path}'s header is {','.join(header)}, not ark,p_path. The scripts "
                      "convert it the next time they write it; check_resolve.py --from-github checks every row.")
    if missing:
        step[0] += 1
        print(f"  NOTE {step[0]}. Not on branch {branch}: {', '.join(missing)}. The scripts would create them on the "
              "first push; if they're there under another name (such as arks_master_list.csv), rename them.")

    if args.no_write:
        print("Read-only checks passed (test push skipped).")
        return 0

    # 6. Test push to a temporary branch
    test_branch = "push-test-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    status, body = call("POST", f"{api}/repos/{repo}/git/refs", token,
                        {"ref": f"refs/heads/{test_branch}", "sha": head_sha})
    if status not in (200, 201):
        return fail(f"Creating a temporary branch failed (HTTP {status}): {message(body)}",
                    "The token can read but not write. " + fix)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(f"Test push from check_github.py at {datetime.datetime.now().isoformat(timespec='seconds')}.\n")
            tmp = f.name
        try:
            ga.push_file_to_github(tmp, api, repo, "push-test.txt", test_branch, token,
                                   "Test push from check_github.py (temporary branch, deleted straight away)")
        except RuntimeError as e:
            return fail(f"The test push failed: {e}", "The token can read but not write. " + fix)
        finally:
            os.remove(tmp)
        ok(f"a test push worked (to the temporary branch {test_branch}; {branch} wasn't touched)")
    finally:
        status, _ = call("DELETE", f"{api}/repos/{repo}/git/refs/heads/{urllib.parse.quote(test_branch, safe='')}", token)
        if status not in (200, 204):
            print(f"  NOTE the temporary branch {test_branch} couldn't be deleted (HTTP {status}); "
                  f"delete it at https://github.com/{repo}/branches.")
        else:
            print(f"       (temporary branch {test_branch} deleted)")
    print("All checks passed: the ARK scripts can pull from and push to GitHub from this computer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
