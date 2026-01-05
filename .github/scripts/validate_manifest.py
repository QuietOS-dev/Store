#!/usr/bin/env python3
# All messages and comments in English

import os
import json
import hashlib
import requests
from github import Github
from PIL import Image
from io import BytesIO

ICON_BASE_URL = "https://raw.githubusercontent.com/QuietOS-dev/Store/refs/heads/main/icons"
ICON_MAX_SIZE = 4096
ICON_WIDTH = 32
ICON_HEIGHT = 32

REQUIRED_FIELDS = [
    "package",
    "name",
    "author",
    "version",
    "category",
    "description",
    "url",
    "sha256",
    "api_level",
    "permissions",
    "min_os_version"
]

MARKER_START = "<!-- MANIFEST_HASHES"
MARKER_END = "END MANIFEST_HASHES -->"

validation_success = True

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

def post_marker_comment(issue, data_dict):
    # remove existing marker comments
    comments = list(issue.get_comments())
    for c in comments:
        if MARKER_START in (c.body or ""):
            try:
                c.delete()
            except Exception:
                pass
    payload = json.dumps(data_dict, ensure_ascii=False, indent=2)
    body = f"{MARKER_START}\n{payload}\n{MARKER_END}"
    issue.create_comment(body)

def validate_icon_for_package(pkg, pr, repo):
    ICON_MAX_SIZE = 4096
    ICON_WIDTH = 32
    ICON_HEIGHT = 32
    errors = []

    # Сначала ищем в PR
    icon_file = None
    for f in pr.get_files():
        if f.filename == f"icons/{pkg}.png":
            icon_file = f
            break

    if icon_file:
        try:
            r = requests.get(icon_file.raw_url, timeout=10)
            r.raise_for_status()
            content = r.content
        except Exception as e:
            return False, f"Failed to download icon from PR: {e}"
    else:
        # fallback в main только для старых иконок
        try:
            content = repo.get_contents(f"icons/{pkg}.png", ref="main").decoded_content
            if isinstance(content, str):
                content = content.encode("utf-8")
        except Exception:
            return False, "Icon not found in PR or main branch"


    # Проверка размера
    if len(content) > ICON_MAX_SIZE:
        return False, f"Icon size exceeds {ICON_MAX_SIZE} bytes"

    # Проверка изображения
    try:
        img = Image.open(BytesIO(content))
        if img.format != "PNG":
            return False, "Icon is not PNG"
        if img.width != ICON_WIDTH or img.height != ICON_HEIGHT:
            return False, f"Icon dimensions must be {ICON_WIDTH}x{ICON_HEIGHT}"
    except Exception as e:
        return False, f"Icon image invalid: {e}"

    return True, None


def main():
    global validation_success

    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo_name:
        print("Missing GitHub environment variables")
        return

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    event = load_event()
    pr_number = get_pr_number(event)
    if not pr_number:
        print("No pull request context")
        return

    pr = repo.get_pull(pr_number)
    issue = repo.get_issue(pr_number)

    changed_files = list(pr.get_files())
    manifests_files = [f for f in changed_files if f.filename.startswith("manifests/") and f.filename.endswith(".json")]

    if not manifests_files:
        issue.create_comment("No manifests found in this PR.")
        validation_success = False
        return

    errors = []
    current_hashes = {}

    for mf in manifests_files:
        fname = mf.filename
        try:
            # Use raw_url if available (works for forks), fallback to repo.get_contents
            if hasattr(mf, "raw_url") and mf.raw_url:
                r = requests.get(mf.raw_url, timeout=10)
                r.raise_for_status()
                file_bytes = r.content
            else:
                content = repo.get_contents(fname, ref=pr.head.ref).decoded_content
                if isinstance(content, str):
                    file_bytes = content.encode("utf-8")
                else:
                    file_bytes = content
        except Exception as e:
            errors.append(f"❌ {fname}: failed to read file from PR branch: {e}")
            validation_success = False
            continue

        current_hashes[fname] = sha256_bytes(file_bytes)

        try:
            manifest = json.loads(file_bytes)
        except Exception as e:
            errors.append(f"❌ {fname}: invalid JSON ({e})")
            validation_success = False
            continue

        for f in REQUIRED_FIELDS:
            if f not in manifest:
                errors.append(f"❌ {fname}: missing required field '{f}'")
                validation_success = False

        pkg = manifest.get("package")
        url = manifest.get("url")
        declared_sha = manifest.get("sha256")

        if pkg:
            ok, reason = validate_icon_for_package(pkg, pr, repo)
            if not ok:
                errors.append(f"❌ manifests/{pkg}.json: icon problem: {reason}")
                validation_success = False
        else:
            errors.append(f"❌ {fname}: package field missing, cannot validate icon")
            validation_success = False

        if url:
            try:
                rr = requests.get(url, timeout=20)
                if rr.status_code != 200:
                    errors.append(f"❌ {fname}: URL returned HTTP {rr.status_code}")
                    validation_success = False
                else:
                    actual = sha256_bytes(rr.content)
                    if declared_sha and actual != declared_sha:
                        errors.append(f"❌ {fname}: sha256 mismatch (expected {declared_sha}, got {actual})")
                        validation_success = False
            except Exception as e:
                errors.append(f"❌ {fname}: failed to download file: {e}")
                validation_success = False
        else:
            errors.append(f"❌ {fname}: url is empty")
            validation_success = False

    labels = [l.name for l in pr.get_labels()]

    if not validation_success:
        if "Under review" in labels:
            try:
                pr.remove_from_labels("Under review")
            except Exception:
                pass
        if "Invalid manifest" not in labels:
            try:
                pr.add_to_labels("Invalid manifest")
            except Exception:
                pass

        issue.create_comment(
            "Manifest validation failed:\n\n" +
            "\n".join(errors) +
            "\n\nFix the issues and then comment `@bot check` to request a new validation."
        )

        # remove stored hashes if exist
        comments = list(issue.get_comments())
        for c in comments:
            if MARKER_START in (c.body or ""):
                try:
                    c.delete()
                except Exception:
                    pass
        return

    # success
    if "Invalid manifest" in labels:
        try:
            pr.remove_from_labels("Invalid manifest")
        except Exception:
            pass
    if "Under review" not in labels:
        try:
            pr.add_to_labels("Under review")
        except Exception:
            pass

    try:
        post_marker_comment(issue, current_hashes)
    except Exception as e:
        issue.create_comment(f"Manifests validated, but failed to store hash marker: {e}\nModerator review required.")
        validation_success = False
        return

    issue.create_comment(
        "✅ Manifest validation successful.\n"
        "The manifest(s) are now locked for moderator review.\n\n"
        "Moderator commands:\n"
        "- `@bot allow` — approve the PR\n"
        "- `@bot deny` — reject the PR"
    )

if __name__ == "__main__":
    main()
