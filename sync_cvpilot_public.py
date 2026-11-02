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
USER_SYSTEMD = HOME / ".config/systemd/user"

# Keep the original source-code behavior: only update existing public files.
# New source files are reported for manual review, not silently published.
GROUPS = {
    "controller": (APP / "controller", {".py"}),
    "discord": (LIVE / "discord", {".py"}),
    "plugin": (EXTENSION, {".js", ".json"}),
    "repo-template": (APP / "repo-template", {".md", ".json", ".gitignore"}),
    "templates": (APP / "templates", {".md", ".sh", ".py", ".json"}),
    "tests": (APP / "tests", {".py"}),
}
SKIP_PARTS = {".git", "__pycache__", ".venv", "node_modules"}

# The only live systemd file allowed into the public repository, and only
# when it contains exactly the expected section/directive (plus comments).
COMPAT_REL = Path("systemd/cvpilot-controller.service.d/30-openclaw-compat.conf")
COMPAT_LIVE = (USER_SYSTEMD / "cvpilot-controller.service.d"
               / "30-openclaw-compat.conf")


def git(*args):
    return subprocess.run(
        ["git", "-C", str(PUBLIC), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def is_allowed(file, suffixes):
    return file.suffix in suffixes or (
        file.name == ".gitignore" and ".gitignore" in suffixes
    )


def source_files(root, suffixes):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"Missing/unsafe source directory: {root}")
    for file in sorted(root.rglob("*")):
        rel = file.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if not is_allowed(file, suffixes):
            continue
        if file.is_symlink() or not file.is_file():
            continue
        yield rel, file


def check_destination(dest):
    """Reject symlinks at all levels, including paths not yet created."""
    if not dest.is_relative_to(PUBLIC):
        raise RuntimeError(f"Destination outside public repository: {dest}")
    current = dest
    while current != PUBLIC:
        if current.is_symlink():
            raise RuntimeError(f"Refusing to follow symlink: {current}")
        current = current.parent
    if dest.exists() and not dest.is_file():
        raise RuntimeError(f"Refusing to overwrite non-file: {dest}")


def validated_override():
    if not COMPAT_LIVE.is_file() or COMPAT_LIVE.is_symlink():
        print(f"WARNING: no safe live compatibility override: {COMPAT_LIVE}")
        return None
    if COMPAT_LIVE.stat().st_size > 8192:
        raise RuntimeError("Systemd compatibility override unexpectedly large")
    content = COMPAT_LIVE.read_text(encoding="utf-8")
    directives = [
        line.strip() for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";"))
    ]
    if directives != ["[Service]", "PrivateTmp=no"]:
        raise RuntimeError(
            "Live 30-openclaw-compat.conf has unexpected directives; "
            "review it manually before publishing"
        )
    # Recreate canonical content instead of copying arbitrary live comments.
    # Comments could otherwise accidentally contain private information.
    return (
        "# CVPilot: OpenClaw CLI/Gateway ownership check compatibility.\n"
        "[Service]\n"
        "PrivateTmp=no\n"
    ).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Copy changes (default: preview only)")
    args = parser.parse_args()

    if PUBLIC.is_symlink() or not PUBLIC.is_dir() or not (PUBLIC / ".git").exists():
        parser.error(f"Git working tree not found: {PUBLIC}")
    try:
        actual_root = Path(git("rev-parse", "--show-toplevel").strip())
        if actual_root != PUBLIC:
            parser.error(f"Unexpected Git root: {actual_root}")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        parser.error(f"Cannot inspect Git repository: {exc}")

    groups = dict(GROUPS)
    if not EXTENSION.is_dir():
        groups["plugin"] = (APP / "plugin", {".js", ".json"})
        print("WARNING: installed extension unavailable; using app/plugin.")
    else:
        print(f"Plugin source: {EXTENSION}")

    # Actions are (destination, source path or None, bytes or None, is_new).
    actions = []
    skipped_new = []
    public_only = []

    for group, (src_root, suffixes) in groups.items():
        dst_root = PUBLIC / group
        if not dst_root.is_dir() or dst_root.is_symlink():
            raise RuntimeError(f"Missing/unsafe public directory: {dst_root}")
        live_relpaths = set()
        for rel, source in source_files(src_root, suffixes):
            live_relpaths.add(rel)
            dest = dst_root / rel
            check_destination(dest)
            if not dest.is_file():
                skipped_new.append(dest.relative_to(PUBLIC))
            elif not filecmp.cmp(source, dest, shallow=False):
                actions.append((dest, source, None, False))
        for dest in sorted(dst_root.rglob("*")):
            if dest.is_file() and not dest.is_symlink():
                rel = dest.relative_to(dst_root)
                if (not any(part in SKIP_PARTS for part in rel.parts)
                        and is_allowed(dest, suffixes) and rel not in live_relpaths):
                    public_only.append(dest.relative_to(PUBLIC))

    src_docker = APP / "Dockerfile"
    dst_docker = PUBLIC / "Dockerfile"
    if (not src_docker.is_file() or src_docker.is_symlink()
            or not dst_docker.is_file() or dst_docker.is_symlink()):
        raise RuntimeError("Missing/unsafe Dockerfile in live or public copy")
    check_destination(dst_docker)
    if not filecmp.cmp(src_docker, dst_docker, shallow=False):
        actions.append((dst_docker, src_docker, None, False))

    # Unlike the original source sync, approved deployment artifacts are
    # created automatically if missing. No entire live systemd directory copied.
    deployment = PUBLIC / "deployment"
    if deployment.is_symlink() or (deployment.exists() and not deployment.is_dir()):
        raise RuntimeError(f"Unsafe deployment destination: {deployment}")
    compat = validated_override()

    print(f"\nMode: {'APPLY' if args.apply else 'DRY RUN (no changes made)'}")
    if actions:
        for dest, _, _, is_new in actions:
            print(f"{'ADD   ' if is_new else 'UPDATE'} {dest.relative_to(PUBLIC)}")
    else:
        print("All managed public files match their sources.")
    if skipped_new:
        print("\nNew live SOURCE files not copied (review manually):")
        for rel in skipped_new:
            print(f"  {rel}")
    if public_only:
        print("\nPublic-only source files not deleted:")
        for rel in public_only:
            print(f"  {rel}")

    if not args.apply:
        print("\nUse --apply to perform ADD and UPDATE actions.")
        return

    conflicts = []
    for dest, _, _, _ in actions:
        rel = str(dest.relative_to(PUBLIC))
        if git("status", "--porcelain", "--", rel).strip():
            conflicts.append(rel)
    if conflicts:
        print("\nRefusing to overwrite paths with Git changes:", file=sys.stderr)
        for rel in conflicts:
            print(f"  {rel}", file=sys.stderr)
        print("Commit, stash, or reconcile those paths first.", file=sys.stderr)
        sys.exit(2)

    for dest, source, content, _ in actions:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source is not None:
            shutil.copy2(source, dest)
        else:
            dest.write_bytes(content)

    print(f"\nCopied {len(actions)} file(s). No Git commit or push performed.")
    print("Review before publishing: git diff; git status --short; git diff --check")
    print("Inspect for credentials and personal information before git add/push.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
