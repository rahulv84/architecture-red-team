"""Build the AI By Design Agentathon upload folder.

Usage (from the repository root):

    python src/scripts/package_submission.py                 # dist/Rahul_Verma_694836/ + .zip
    python src/scripts/package_submission.py --name A_B_123  # another folder name
    python src/scripts/package_submission.py --no-zip

The folder follows the required layout (Agent.md, README.md, src/, tests/, docs/ plus setup files). Local state and
secrets are never copied: .env, data/, .venv, .git, caches, checkpoints and persisted settings are excluded. Before
anything is written, every file is scanned. Packaging stops if a value from your real .env, or anything that looks like
a real key, would be included. Exit code: 0 packaged, 1 secrets found, 2 invalid input.
"""
from __future__ import annotations

import argparse
import fnmatch
import logging
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NAME = "Rahul_Verma_694836"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'-]*_[A-Za-z][A-Za-z'-]*_\d+$")  # <Firstname>_<Lastname>_<EmpID>
REQUIRED = ("Agent.md", "README.md", "src", "tests", "docs")
INCLUDE = (*REQUIRED, "requirements.txt", "pyproject.toml", ".env.example", ".gitignore", "CLAUDE.md")
EXCLUDE = ("*/__pycache__/*", "*.pyc", "*/.pytest_cache/*", "*/.ruff_cache/*", ".env", "*/.env", "*.sqlite*",
           "*/settings.json", "data/*", "dist/*", ".venv/*", ".git/*")

logger = logging.getLogger("package_submission")

# Formats of real credentials (always blocking unless the exact value is a known test placeholder).
KEY_PATTERNS = {
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "Bedrock API key": re.compile(r"\bABSK[A-Za-z0-9+/=]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}
# Placeholders used by the test suite; they are not credentials.
KNOWN_TEST_VALUES = {"sk-ant-abcdefghijklmnopqrstuvwxyz", "sk-ant-test-123456789012345", "sk-ant-secret-123456789012345"}
# Hard-coded passwords: a warning only, because the demo fixtures contain planted fake ones for the critics to find.
PASSWORD_ASSIGNMENT = re.compile(r"(?i)\b(password|passwd|secret)\b[\"']?\s*[:=]\s*[\"']([^\"'\s$<{]{6,})[\"']")
SECRET_ENV_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD)")


@dataclass
class Hit:
    path: str
    line: int
    kind: str
    blocking: bool


def candidate_files(root: Path) -> list[Path]:
    """Files to package: git-visible (tracked or untracked, not ignored) when git is available, else a directory walk."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True, text=True, check=True).stdout
        paths = [root / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in root.rglob("*") if p.is_file()]
    selected = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.split("/")[0] not in INCLUDE:
            continue
        if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("/" + rel, pat) for pat in EXCLUDE):
            continue
        selected.append(path)
    return sorted(selected)


def env_secret_values(env_file: Path) -> list[str]:
    """Values of secret-looking variables in the real .env (never logged)."""
    if not env_file.is_file():
        return []
    values = []
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, _, value = raw.partition("=")
        value = value.strip().strip('"').strip("'")
        if SECRET_ENV_NAME.search(key) and len(value) >= 8:
            values.append(value)
    return values


def _line_findings(line: str, env_values: list[str]) -> list[tuple[str, bool]]:
    """(kind, blocking) for one line of text."""
    if any(v in line for v in env_values):
        return [("value from your .env", True)]
    found = [(kind, True) for kind, rx in KEY_PATTERNS.items()
             if any(m.group(0) not in KNOWN_TEST_VALUES for m in rx.finditer(line))]
    if PASSWORD_ASSIGNMENT.search(line):
        found.append(("hard-coded password (fake demo flaw?)", False))
    return found


def scan(root: Path, files: list[Path], env_values: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files (images) are copied as they are
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits.extend(Hit(rel, lineno, kind, blocking) for kind, blocking in _line_findings(line, env_values))
    return hits


def build(root: Path, files: list[Path], target: Path, make_zip: bool) -> Path | None:
    if target.exists():
        shutil.rmtree(target)
    for path in files:
        dest = target / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    if not make_zip:
        return None
    archive = target.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(target.parent).as_posix())
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Agentathon submission folder (no secrets, no local state).")
    parser.add_argument("--name", default=DEFAULT_NAME, help="folder name, <Firstname>_<Lastname>_<EmpID>")
    parser.add_argument("--out", default="dist", help="output directory, relative to the repository root (default: dist)")
    parser.add_argument("--no-zip", action="store_true", help="do not create the .zip archive")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not NAME_RE.match(args.name):
        logger.error("Folder name %r does not follow <Firstname>_<Lastname>_<EmpID>.", args.name)
        return 2
    out_dir = (ROOT / args.out).resolve()
    if out_dir == ROOT or ROOT not in out_dir.parents:
        logger.error("--out must be a sub-directory of the repository (got %s).", out_dir)
        return 2
    missing = [r for r in REQUIRED if not (ROOT / r).exists()]
    if missing:
        logger.error("Required by the submission format but missing: %s", ", ".join(missing))
        return 2

    files = candidate_files(ROOT)
    hits = scan(ROOT, files, env_secret_values(ROOT / ".env"))
    for hit in hits:
        (logger.error if hit.blocking else logger.warning)("  %s %s:%d  %s", "BLOCK" if hit.blocking else "warn ", hit.path, hit.line, hit.kind)
    if any(h.blocking for h in hits):
        logger.error("Possible secrets found; nothing was packaged. Replace them with placeholders and run again.")
        return 1

    target = out_dir / args.name
    try:
        archive = build(ROOT, files, target, not args.no_zip)
    except OSError as exc:
        logger.error("Could not write %s: %s. Close any terminal, editor or Explorer window using it and run again.",
                     target, exc.strerror or exc)
        return 2
    logger.info("Packaged %d files into %s", len(files), target)
    if archive:
        logger.info("Archive: %s (%.1f MB)", archive, archive.stat().st_size / 1e6)
    logger.info("Upload the contents to your assigned folder in the AI By Design Agent Library on SharePoint.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
