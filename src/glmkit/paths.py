"""Where the kit finds its data: conformance cases, endpoint profiles, results."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent  # valid for a source checkout / editable install

CASES_ENV = "GLMKIT_CASES_DIRS"  # os.pathsep-separated extra case roots
PROFILES_ENV = "GLMKIT_PROFILES_DIRS"  # os.pathsep-separated extra profile folders


def _env_dirs(name: str) -> list[Path]:
    return [Path(p) for p in os.environ.get(name, "").split(os.pathsep) if p.strip()]


def default_case_roots() -> list[Path]:
    """Case roots, in priority order: $GLMKIT_CASES_DIRS, the repo's ``cases/``, the wheel-bundled copy."""
    roots = _env_dirs(CASES_ENV)
    repo_cases = REPO_ROOT / "cases"
    bundled = PACKAGE_DIR / "_bundled" / "cases"
    if repo_cases.is_dir():
        roots.append(repo_cases)
    elif bundled.is_dir():
        roots.append(bundled)
    return roots


def builtin_profiles_dir() -> Path:
    return PACKAGE_DIR / "profiles"


def profile_dirs() -> list[Path]:
    """Profile folders, later entries override earlier ones by name: built-in, ./profiles, $GLMKIT_PROFILES_DIRS."""
    dirs = [builtin_profiles_dir()]
    local = Path.cwd() / "profiles"
    if local.is_dir() and local.resolve() != builtin_profiles_dir().resolve():
        dirs.append(local)
    return dirs + _env_dirs(PROFILES_ENV)
