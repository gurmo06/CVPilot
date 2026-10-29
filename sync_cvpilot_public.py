#!/usr/bin/env python3
"""One-way, allowlisted CVPilot source sync: live installation -> public Git repo.

Dry-run by default. Use --apply to copy changed files. Never deletes files,
commits, pushes, or copies credentials/runtime state.
"""

import argparse
import filecmp
from pathlib import Path
import shutil
import subprocess
import sys

HOME = Path.home()
LIVE = HOME / ".local/share/cvpilot"
APP = LIVE / "app"
PUBLIC = HOME / "CVPilot-public"
EXTENSION = HOME / ".openclaw/extensions/cvpilot"

# Only existing files in these public-repo directories are synchronized.
# New files need to be reviewed and added to the public repository manually.
GROUPS = {
    "controller": (APP / "controller", {".py"}),
    "discord": (LIVE / "discord", {".py"}),
    "plugin": (EXTENSION, {".js", ".json"}),
    "repo-template": (APP / "repo-template", {".md", ".json", ".gitignore"}),
    "templates": (APP / "templates", {".md", ".sh", ".py", ".json"}),
    "tests": (APP / "tests", {".py"}),
}
SKIP_PARTS = {".git", "__pycache__", ".venv", "node_modules"}


def git(*args):
    return subprocess.run(
        ["git", "-C", str(PUBLIC), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def is_allowed(file, suffixes):
    return file.suffix in suffixes or (file.name == ".gitignore" and ".gitignore" in suffixes)


def source_files(root, suffixes):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"Expected source directory is missing or a symlink: {root}")
    for file in sorted(root.rglob("*")):
        rel = file.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if not is_allowed(file, suffixes) or file.is_symlink() or not file.is_file():
            continue
        yield rel, file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Copy changes (default: preview only)")
    args = parser.parse_args()

    if not PUBLIC.is_dir() or not (PUBLIC / ".git").exists():
        parser.error(f"Git repository not found: {PUBLIC}")
    try:
        git("rev-parse", "--show-toplevel")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        parser.error(f"Cannot inspect Git repository: {exc}")

    # The installed OpenClaw extension is authoritative if present. Otherwise
    # fall back to the plugin bundled with the live application, with a warning.
    if not EXTENSION.is_dir():
        GROUPS["plugin"] = (APP / "plugin", {".js", ".json"})
        print("WARNING: installed OpenClaw plugin not found; using app/plugin instead.")
    else:
        print(f"Plugin source: {EXTENSION}")

    changes = []
    skipped_new = []
    public_only = []
    for group, (src_root, suffixes) in GROUPS.items():
        dst_root = PUBLIC / group
        if not dst_root.is_dir() or dst_root.is_symlink():
            raise RuntimeError(f"Expected public directory is missing or a symlink: {dst_root}")
        live_relpaths = set()
        for rel, source in source_files(src_root, suffixes):
            live_relpaths.add(rel)
            dest = dst_root / rel
            if dest.is_symlink():
                raise RuntimeError(f"Refusing to overwrite symlink: {dest}")
            if not dest.is_file():
                skipped_new.append(dest.relative_to(PUBLIC))
                continue
            if not filecmp.cmp(source, dest, shallow=False):
                changes.append((source, dest))
        for dest in sorted(dst_root.rglob("*")):
            if dest.is_file() and not dest.is_symlink():
                rel = dest.relative_to(dst_root)
                if (not any(part in SKIP_PARTS for part in rel.parts)
                        and is_allowed(dest, suffixes) and rel not in live_relpaths):
                    public_only.append(dest.relative_to(PUBLIC))

    src_docker = APP / "Dockerfile"
    dst_docker = PUBLIC / "Dockerfile"
    if not src_docker.is_file() or src_docker.is_symlink():
        raise RuntimeError(f"Missing or unsafe Dockerfile: {src_docker}")
    if not dst_docker.is_file() or dst_docker.is_symlink():
        raise RuntimeError(f"Missing or unsafe public Dockerfile: {dst_docker}")
    if not filecmp.cmp(src_docker, dst_docker, shallow=False):
        changes.append((src_docker, dst_docker))

    print(f"\nMode: {'APPLY' if args.apply else 'DRY RUN (no changes made)'}")
    if changes:
        for source, dest in changes:
            print(f"UPDATE  {dest.relative_to(PUBLIC)}")
    else:
        print("All managed public files already match live sources.")

    if skipped_new:
        print("\nNew live files NOT copied (review and add these manually):")
        for rel in skipped_new:
            print(f"  {rel}")

    if public_only:
        print("\nPublic-only managed files NOT deleted (review manually):")
        for rel in public_only:
            print(f"  {rel}")

    if not args.apply:
        print("\nUse --apply to synchronize the UPDATE entries.")
        return

    conflicts = []
    for _, dest in changes:
        rel = str(dest.relative_to(PUBLIC))
        if git("status", "--porcelain", "--", rel).strip():
            conflicts.append(rel)
    if conflicts:
        print("\nRefusing to overwrite files with uncommitted/staged Git changes:", file=sys.stderr)
        for rel in conflicts:
            print(f"  {rel}", file=sys.stderr)
        print("Commit, stash, or reconcile those changes first.", file=sys.stderr)
        sys.exit(2)

    for source, dest in changes:
        shutil.copy2(source, dest)
    print(f"\nUpdated {len(changes)} file(s) in {PUBLIC}.")
    print("No Git commit/push performed. Review with: git diff && git status --short")
    print("Review for secrets and personal information BEFORE git add/commit/push.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
