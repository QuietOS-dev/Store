#!/usr/bin/env python3
# Bot command handler; messages in English.

import os
import sys
import json
import subprocess
import hashlib
from github import Github

MARKER_START = "<!-- MANIFEST_HASHES"
MARKER_END = "END MANIFEST_HASHES -->"

MODERATOR = os.environ.get("BOT_USER", "Artyomka628")
MODERATOR2 = os.environ.get("BOT_USER2", "QuietOS-dev")

def sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()

def load_event():
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_pr_number(event):
    if "pull_request" in event and event["pull_request"]:
        return event["pull_request"]["number"]
    if "issue" in event and event["issue"].get("pull_request"):
        return event["issue"]["number"]
    return None

def find_marker_comment(comments):
    for c in reversed(comments):
        body = c.body or ""
        if MARKER_START in body and MARKER_END in body:
            try:
                start = body.index(MARKER_START) + len(MARKER_START)
                end = body.index(MARKER_END, start)
                json_text = body[start:end].strip()
                data = json.loads(json_text)
                return c, data
            except Exception:
                continue
    return None, None

def compute_current_hashes(repo, pr):
    hashes = {}
    files = list(pr.get_files())
    manifests = [f for f in files if f.filename.startswith("manifests/") and f.filename.endswith(".json")]
    for mf in manifests:
        fname = mf.filename
        try:
            content = repo.get_contents(fname, ref=pr.head.ref).decoded_content
            if isinstance(content, str):
                b = content.encode("utf-8")
            else:
                b = content
            hashes[fname] = sha256_bytes(b)
        except Exception:
            hashes[fname] = None
    return hashes

def remove_labels(pr, labels_to_remove):
    existing = [l.name for l in pr.get_labels()]
    for lab in labels_to_remove:
        if lab in existing:
            try:
                pr.remove_from_labels(lab)
            except Exception:
                pass

def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo_name:
        print("Missing env")
        return

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    event = load_event()
    pr_number = get_pr_number(event)
    if not pr_number:
        print("No PR context")
        return

    pr = repo.get_pull(pr_number)
    issue = repo.get_issue(pr_number)

    # extract comment body and author
    comment_body = ""
    comment_user = ""
    if "comment" in event and event["comment"]:
        comment_body = event["comment"].get("body", "").strip()
        comment_user = event["comment"].get("user", {}).get("login", "")
    else:
        comment_body = os.environ.get("GITHUB_EVENT_COMMENT_BODY", "")
        comment_user = os.environ.get("GITHUB_ACTOR", "")

    # only act when @bot is mentioned
    if "@bot" not in comment_body:
        # If triggered by pull_request event, run validate_manifest directly
        if "pull_request" in event:
            # run validate flow
            subprocess.run(["python", ".github/scripts/validate_manifest.py"], env=os.environ)
        return

    # handle @bot check
    if "@bot check" in comment_body:
        issue.create_comment(f"🔁 Running manifest validation as requested by @{comment_user}...")
        res = subprocess.run(["python", ".github/scripts/validate_manifest.py"], env=os.environ)
        if res.returncode == 0:
            issue.create_comment("✅ Validation completed.")
        else:
            issue.create_comment("⚠️ Validation finished with internal note. See comments above.")
        return

    # handle allow
    if "@bot allow" in comment_body:
        if comment_user != MODERATOR and comment_user != MODERATOR2:
            issue.create_comment(f"❌ Only @{MODERATOR} or @{MODERATOR2} can approve or deny PRs.")
            return

    # просто удаляем старые лейблы и ставим Approved
    remove_labels(pr, ["Invalid manifest", "Under review", "Rejected"])
    try:
        pr.add_to_labels("Approved")
    except Exception:
        pass

    issue.create_comment("✅ PR has been approved by moderator.")
    return

    # handle deny
    if "@bot deny" in comment_body:
        if comment_user != MODERATOR and comment_user != MODERATOR2:
            issue.create_comment(f"DEBUG: comment_user = '{comment_user}'")
            issue.create_comment(f"❌ Only @{MODERATOR} or @{MODERATOR2} can approve or deny PRs.")
            return

        remove_labels(pr, ["Invalid manifest", "Under review", "Approved"])
        try:
            pr.add_to_labels("Rejected")
        except Exception:
            pass
        issue.create_comment("❌ PR has been rejected by moderator.")
        return

    # unknown command
    issue.create_comment("ℹ️ Unknown command. Supported: `@bot check`, `@bot allow`, `@bot deny`.")

if __name__ == "__main__":
    main()
