#!/usr/bin/env python3
"""
check_gitlab.py
===============

Test the GitLab connection the ARK scripts use, step by step, without changing
resolve.csv or anything else on the main branch.

    export GITLAB_TOKEN=glpat-...          (PowerShell: $env:GITLAB_TOKEN = "glpat-...")
    python3 check_gitlab.py                # every check, including a test push
    python3 check_gitlab.py --no-write     # read-only checks

It checks, in order, and stops at the first problem with an explanation:

  1. git.uni.edu can be reached from this computer;
  2. the token is accepted;
  3. the token can see the project (default rod-library/resolver), and with
     which role;
  4. the branch (default main) exists;
  5. resolve.csv and ark_master_list.csv can be read from it;
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

if not hasattr(ga, "fetch_file_from_gitlab"):
    sys.exit(f"The generate_arks.py next to this script ({ga.__file__}) is an older version. "
             "Download the current one from https://github.com/rodlibrary/Scripts/tree/main/arks.")

ROLES = {10: "Guest", 15: "Planner", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}


def call(method: str, url: str, token: str = "", body: "dict | None" = None, timeout: int = 30):
    """(HTTP status, parsed JSON or text). Status 0 means the server couldn't be reached."""
    headers = {"User-Agent": "check_gitlab.py"}
    if token:
        headers["PRIVATE-TOKEN"] = token
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Test the GitLab connection used by the ARK scripts.")
    ap.add_argument("--gitlab-url", default=os.environ.get("GITLAB_URL") or ga.DEFAULT_GITLAB_URL)
    ap.add_argument("--gitlab-project", default=os.environ.get("GITLAB_PROJECT") or ga.DEFAULT_GITLAB_PROJECT)
    ap.add_argument("--gitlab-branch", default=os.environ.get("GITLAB_BRANCH") or "main")
    ap.add_argument("--gitlab-token-env", default="GITLAB_TOKEN")
    ap.add_argument("--no-write", action="store_true", help="skip the test push")
    args = ap.parse_args()

    base = args.gitlab_url.rstrip("/")
    api = f"{base}/api/v4"
    project = args.gitlab_project
    pid = urllib.parse.quote(project, safe="")
    branch = args.gitlab_branch
    token = os.environ.get(args.gitlab_token_env, "").strip()
    step = [0]

    def ok(msg):
        step[0] += 1
        print(f"  OK   {step[0]}. {msg}")

    def fail(msg, fix):
        step[0] += 1
        print(f"  FAIL {step[0]}. {msg}\n         {fix}")
        return 1

    print(f"Checking {base}/{project} (branch {branch})")

    # 1. Reachable
    status, body = call("GET", f"{api}/version")
    if status == 0:
        return fail(f"Can't reach {base}: {body}",
                    "This computer can't connect to the GitLab server. On campus, check the network; "
                    "off campus, connect to the university VPN first.")
    ok(f"{base} can be reached")

    # 2. Token
    if not token:
        return fail(f"{args.gitlab_token_env} isn't set",
                    'Set it first: PowerShell  $env:GITLAB_TOKEN = "glpat-..."   Mac/Linux  export GITLAB_TOKEN=glpat-...')
    status, body = call("GET", f"{api}/projects/{pid}", token)
    if status == 401:
        return fail("GitLab rejected the token (HTTP 401)",
                    "The token is wrong, expired or revoked. Create a new project access token in the project's "
                    "Settings > Access tokens (Developer role, api scope), and update the GitHub secret too.")
    ok("the token is accepted")

    # 3. Project
    if status == 404:
        return fail(f"The token can't see {project} (HTTP 404)",
                    f"Check the project path in its address ({base}/{project}). A project access token only works "
                    "for the project it was created in.")
    if status != 200:
        return fail(f"Reading {project} failed (HTTP {status}): {str(body)[:200]}", "See the message above.")
    perms = (body.get("permissions") or {}) if isinstance(body, dict) else {}
    level = max((perms.get(k) or {}).get("access_level", 0) for k in ("project_access", "group_access")) if perms else 0
    role = ROLES.get(level, "unknown role")
    default_branch = body.get("default_branch") if isinstance(body, dict) else None
    ok(f"the token can see {body.get('path_with_namespace', project)} with the {role} role"
       + (f" (default branch {default_branch})" if default_branch else ""))
    if level and level < 30:
        return fail(f"The {role} role can't push",
                    "Pushing needs the Developer role or higher: create the token again with the Developer role.")

    # 4. Branch
    status, body = call("GET", f"{api}/projects/{pid}/repository/branches/{urllib.parse.quote(branch, safe='')}", token)
    if status == 404:
        return fail(f"There's no branch {branch!r}",
                    f"The project's default branch is {default_branch!r}: use --gitlab-branch {default_branch}"
                    if default_branch else "Check the branch name.")
    if status != 200:
        return fail(f"Reading branch {branch} failed (HTTP {status})", "See the message above.")
    protected = isinstance(body, dict) and body.get("protected")
    ok(f"branch {branch} exists" + (" and is protected" if protected else ""))
    # GitLab says whether this token may push to the branch. main is protected by default
    # so that only Maintainers can push to it.
    if isinstance(body, dict) and body.get("can_push") is False:
        return fail(f"The token isn't allowed to push to {branch}",
                    f"{branch} is protected and the token's role ({role}) isn't allowed to push to it. Either "
                    "create the token with the Maintainer role, or in Settings > Repository > Protected branches "
                    f"set 'Allowed to push and merge' for {branch} to 'Developers + Maintainers'.")
    if isinstance(body, dict) and body.get("can_push"):
        ok(f"the token is allowed to push to {branch}")

    # 5. Files
    missing = []
    for path in (ga.DEFAULT_RESOLVE_MANIFEST, ga.DEFAULT_MASTER_LIST):
        try:
            data = ga.fetch_file_from_gitlab(base, project, path, branch, token)
        except RuntimeError as e:
            return fail(f"Reading {path} failed: {e}", "See the message above.")
        if data is None:
            missing.append(path)
        else:
            rows = max(0, len(data.decode("utf-8-sig", "replace").strip().splitlines()) - 1)
            ok(f"{path} can be read ({rows} row(s))")
    if missing:
        step[0] += 1
        print(f"  NOTE {step[0]}. Not on branch {branch}: {', '.join(missing)}. The scripts would create them on the "
              "first push; if they're there under another name (such as arks_master_list.csv), rename them.")

    if args.no_write:
        print("Read-only checks passed (test push skipped).")
        return 0

    # 6. Test push to a temporary branch
    test_branch = "push-test-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    status, body = call("POST", f"{api}/projects/{pid}/repository/branches", token,
                        {"branch": test_branch, "ref": branch})
    if status not in (200, 201):
        return fail(f"Creating a temporary branch failed (HTTP {status}): {str(body)[:200]}",
                    "The token needs the api scope and the Developer role.")
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(f"Test push from check_gitlab.py at {datetime.datetime.now().isoformat(timespec='seconds')}.\n")
            tmp = f.name
        try:
            ga.push_file_to_gitlab(tmp, base, project, "push-test.txt", test_branch, token,
                                   "Test push from check_gitlab.py (temporary branch, deleted straight away)")
        except RuntimeError as e:
            return fail(f"The test push failed: {e}",
                        "The token can read but not write. It needs the api scope (read_repository isn't enough) "
                        "and the Developer role.")
        finally:
            os.remove(tmp)
        ok(f"a test push worked (to the temporary branch {test_branch}; {branch} wasn't touched)")
    finally:
        status, _ = call("DELETE", f"{api}/projects/{pid}/repository/branches/{urllib.parse.quote(test_branch, safe='')}", token)
        if status not in (200, 204):
            print(f"  NOTE the temporary branch {test_branch} couldn't be deleted (HTTP {status}); "
                  "delete it in GitLab under Code > Branches.")
        else:
            print(f"       (temporary branch {test_branch} deleted)")
    print("All checks passed: the ARK scripts can pull from and push to GitLab from this computer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
