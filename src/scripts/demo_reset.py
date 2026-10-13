"""Reset local state and run pre-demo checks.

Usage (from the repository root, with the app stopped):

    python src/scripts/demo_reset.py                  # show what will be removed, confirm, clean, run checks
    python src/scripts/demo_reset.py --yes            # no confirmation prompt
    python src/scripts/demo_reset.py --dry-run        # report only; delete nothing
    python src/scripts/demo_reset.py --connectivity   # also send one tiny request to the live provider
    python src/scripts/demo_reset.py --checks-only    # skip the clean-up

Cleaning removes everything the app wrote under the data directory (uploads, reports, snapshots,
LangGraph checkpoints, logs and, unless --keep-settings, the persisted settings overrides) while keeping
the ``.gitkeep`` placeholders and ``data/README.md``.  It never touches the repository, ``.env`` or
the demo fixtures.  Exit code: 0 ready, 1 a check failed, 2 the clean-up was refused.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import shutil
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.core.config import EDITABLE_FIELDS, ROOT_DIR, Settings  # noqa: E402
from backend.core.exceptions import AppError  # noqa: E402
from backend.llm.provider_factory import create_provider  # noqa: E402
from backend.parsers.parser_registry import detect_type, parse_bytes  # noqa: E402
from backend.parsers.types import ParserLimits  # noqa: E402

logger = logging.getLogger("demo_reset")

STATE_DIRS = ("uploads", "reports", "snapshots", "checkpoints", "logs")
KEEP_NAMES = {".gitkeep"}
SETTINGS_FILE = "settings.json"


# --------------------------------------------------------------------------- clean-up
@dataclass
class CleanPlan:
    data_dir: Path
    targets: list[Path] = field(default_factory=list)
    total_bytes: int = 0


def _size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def unsafe_data_dir_reason(data_dir: Path, repo_root: Path = ROOT_DIR) -> str | None:
    """Why ``data_dir`` must not be wiped, or None when it is safe."""
    resolved = data_dir.resolve()
    if not resolved.is_dir():
        return f"{resolved} does not exist or is not a directory"
    if resolved == repo_root.resolve() or resolved in repo_root.resolve().parents:
        return f"{resolved} is the repository root or one of its parents"
    if resolved == Path.home().resolve() or resolved.parent == resolved:
        return f"{resolved} is a home or filesystem root directory"
    if not any((resolved / name).is_dir() for name in STATE_DIRS):
        return f"{resolved} does not look like an app data directory (none of {', '.join(STATE_DIRS)} found)"
    return None


def plan_clean(data_dir: Path, keep_settings: bool) -> CleanPlan:
    plan = CleanPlan(data_dir=data_dir.resolve())
    for name in STATE_DIRS:
        folder = plan.data_dir / name
        if folder.is_dir():
            plan.targets.extend(sorted(p for p in folder.iterdir() if p.name not in KEEP_NAMES))
    settings_file = plan.data_dir / SETTINGS_FILE
    if not keep_settings and settings_file.is_file():
        plan.targets.append(settings_file)
    plan.total_bytes = sum(_size(p) for p in plan.targets)
    return plan


def execute_clean(plan: CleanPlan) -> list[str]:
    """Delete the planned paths; returns error messages for anything that could not be removed."""
    errors: list[str] = []
    for path in plan.targets:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            errors.append(f"{path.relative_to(plan.data_dir)}: {exc.strerror or exc}")
    return errors


def ports_in_use(settings: Settings) -> list[int]:
    busy = []
    for port in (settings.fastapi_port, settings.streamlit_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((settings.bind_host, port)) == 0:
                busy.append(port)
    return busy


# --------------------------------------------------------------------------- checks
@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False  # ok=True + warning=True -> worth reading, not blocking


def check_fixtures(demo_dir: Path) -> list[Check]:
    """Every scenario file parses; flag files that scenario.json does not reference."""
    checks: list[Check] = []
    limits = ParserLimits()
    for meta_path in sorted((demo_dir / "fixtures").glob("*/scenario.json")):
        folder = meta_path.parent
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        listed = [(folder, f["filename"]) for f in meta.get("files", [])]
        listed += [(folder / "revision-1", f["filename"]) for f in meta.get("revision", {}).get("files", [])]
        failures = []
        for base, filename in listed:
            path = base / filename
            try:
                data = path.read_bytes()
                parse_bytes(data, filename, detect_type(filename, data), limits)
            except (OSError, AppError) as exc:
                failures.append(f"{path.relative_to(demo_dir)}: {getattr(exc, 'message', None) or exc}")
        checks.append(Check(f"fixtures/{folder.name}", not failures,
                            "; ".join(failures) if failures else f"{len(listed)} file(s) parse"))
        referenced = {name for base, name in listed if base == folder} | {"scenario.json"}
        stray = sorted(p.name for p in folder.iterdir() if p.is_file() and p.name not in referenced)
        if stray:
            checks.append(Check(f"fixtures/{folder.name} extra files", True,
                                f"not part of the scenario (ignored by the app): {', '.join(stray)}", warning=True))
    return checks


def check_hcl_package() -> Check:
    try:
        importlib.metadata.version("bc-python-hcl2")
    except importlib.metadata.PackageNotFoundError:
        return Check("python-hcl2", True, f"python-hcl2 {importlib.metadata.version('python-hcl2')}")
    return Check("python-hcl2", True, "bc-python-hcl2 (pulled in by checkov) is also installed and can overwrite the 'hcl2' "
                 "module on reinstall; if Terraform parsing fails run: pip install --force-reinstall --no-deps python-hcl2",
                 warning=True)


def check_persisted_settings(settings: Settings, data_dir: Path) -> Check | None:
    path = data_dir / SETTINGS_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    differing = sorted(k for k in EDITABLE_FIELDS if k in saved and saved[k] != getattr(settings, k))
    if not differing:
        return None
    return Check(SETTINGS_FILE, True, f"saved UI settings override .env for: {', '.join(differing)}", warning=True)


def check_provider(settings: Settings, connectivity: bool) -> Check:
    name = settings.llm_provider
    if name == "mock":
        return Check("provider", True, "LLM_PROVIDER=mock: only the offline demo is available", warning=True)
    try:
        provider = create_provider(name, settings)
    except AppError as exc:
        return Check("provider", False, exc.message)
    result = provider.check_connectivity() if connectivity else provider.validate_configuration()
    suffix = "" if connectivity else " (use --connectivity to also test the live endpoint)"
    return Check(f"provider {name} ({result.model_id})", result.ok, result.message + (suffix if result.ok else ""))


def run_checks(settings: Settings, connectivity: bool) -> list[Check]:
    checks = [check_hcl_package(), *check_fixtures(settings.demo_dir)]
    persisted = check_persisted_settings(settings, settings.data_dir)
    if persisted:
        checks.append(persisted)
    checks.append(check_provider(settings, connectivity))
    return checks


# --------------------------------------------------------------------------- CLI
def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        logger.error("Not an interactive terminal; re-run with --yes to confirm.")
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _clean(settings: Settings, args: argparse.Namespace) -> bool:
    data_dir = settings.data_dir
    reason = unsafe_data_dir_reason(data_dir)
    if reason:
        logger.error("Refusing to clean: %s.", reason)
        return False
    busy = ports_in_use(settings)
    if busy and not args.dry_run:
        logger.error("The app appears to be running (port(s) %s in use). Stop it first (Ctrl+C in the src/run.py terminal).",
                     ", ".join(map(str, busy)))
        return False
    plan = plan_clean(data_dir, args.keep_settings)
    if not plan.targets:
        logger.info("Data directory %s is already clean.", plan.data_dir)
        return True
    logger.info("Data directory %s: %d item(s), %.1f MB to remove.", plan.data_dir, len(plan.targets), plan.total_bytes / 1e6)
    for name in STATE_DIRS:
        count = sum(1 for p in plan.targets if p.parent.name == name)
        if count:
            logger.info("  %-12s %d item(s)", name, count)
    if any(p.name == SETTINGS_FILE for p in plan.targets):
        logger.info("  %-12s persisted UI overrides (use --keep-settings to keep)", SETTINGS_FILE)
    if args.dry_run:
        logger.info("Dry run: nothing deleted.")
        return True
    if not args.yes and not _confirm("Delete these items?"):
        logger.info("Aborted; nothing deleted.")
        return False
    errors = execute_clean(plan)
    for err in errors:
        logger.error("Could not remove %s", err)
    if not errors:
        logger.info("Removed %d item(s).", len(plan.targets))
    return not errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset local app state and run pre-demo checks.")
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--dry-run", action="store_true", help="report what would be removed; delete nothing")
    parser.add_argument("--keep-settings", action="store_true", help=f"keep data/{SETTINGS_FILE} (persisted UI overrides)")
    parser.add_argument("--checks-only", action="store_true", help="skip the clean-up, only run the checks")
    parser.add_argument("--skip-checks", action="store_true", help="only clean up")
    parser.add_argument("--connectivity", action="store_true", help="send one small request to the configured live provider")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("botocore", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    settings = Settings()
    if not args.checks_only and not _clean(settings, args):
        return 2
    if args.skip_checks:
        return 0

    logger.info("\nPre-demo checks")
    checks = run_checks(settings, args.connectivity)
    for c in checks:
        mark = "FAIL" if not c.ok else ("WARN" if c.warning else " OK ")
        logger.info("  [%s] %s: %s", mark, c.name, c.detail)
    failed = [c for c in checks if not c.ok]
    logger.info("\n%s", "Ready for the demo. Start the app with: python src/run.py" if not failed
                else f"{len(failed)} check(s) failed; fix them before the demo.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
