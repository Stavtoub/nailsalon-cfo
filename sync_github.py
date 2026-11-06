# -*- coding: utf-8 -*-
"""
Permanent auto-sync script to push local changes to GitHub repository Stavtoub/nailsalon-cfo.
Streamlit Community Cloud automatically redeploys from the main branch.
"""
import os
import base64
import json
import tomllib
import urllib.request
import urllib.error

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(WORKSPACE, ".streamlit", "secrets.toml")
REPO = "Stavtoub/nailsalon-cfo"

# 1. Load GitHub token from .streamlit/secrets.toml
with open(SECRETS_PATH, "rb") as f:
    secrets = tomllib.load(f)

gh_token = secrets.get("GITHUB_TOKEN")
if not gh_token:
    raise ValueError("GITHUB_TOKEN not found in .streamlit/secrets.toml")

headers = {
    "Authorization": f"Bearer {gh_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "NailSalon-CFO-Sync"
}

FILES_TO_SYNC = ["app.py", "mydata_processor.py", "requirements.txt", ".gitignore", "PROJECT_INFO.md", "README.md", ".streamlit/config.toml"]

print(f"🚀 Starting GitHub sync to https://github.com/{REPO} (branch: main)...")

for fname in FILES_TO_SYNC:
    fpath = os.path.join(WORKSPACE, fname)
    if not os.path.exists(fpath):
        continue

    with open(fpath, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    url = f"https://api.github.com/repos/{REPO}/contents/{fname}"
    sha = None
    try:
        req_get = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req_get) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"❌ GET error for {fname}: {e.code} {e.read().decode('utf-8')}")
            raise e

    payload = {
        "message": f"Fix P&L Comparison screens (IndexingError & NameError) & eliminate payroll double-counting in Fixed OPEX ({fname})",
        "content": content_b64,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    req_put = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT"
    )
    with urllib.request.urlopen(req_put) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        commit_sha = res_data["commit"]["sha"][:7]
        print(f"✅ Synced {fname} ➔ Commit {commit_sha}")

print("🎉 All files synced to GitHub! Streamlit Community Cloud will auto-deploy shortly.")
