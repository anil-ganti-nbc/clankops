"""Great Clank Census: discover candidates without mutating them."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from clankops.gitinspect import inspect_git
from clankops.redact import redact_url

SECRET_FILENAMES = {".env", ".env.local", ".env.production", "credentials.json", "secrets.yaml"}
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "uv-cache",
    "uv-cache-lint",
    "diag_venv",
}

DEFAULT_ROOTS = [
    Path(r"C:\Users\anil\Clanks"),
    Path(r"C:\Users\anil\Clanks_\Launchers"),
    Path(r"C:\Users\anil\Desktop"),
    Path(r"C:\Users\anil\Documents\Default Project"),
    Path(r"C:\Users\anil\dau-ecosystem"),
    Path(r"C:\Users\anil\Clank Base"),
    Path(r"C:\Users\anil\chudbox"),
]

DESKTOP_INTERESTING = {
    "cvc clank",
    "cvc workbench",
    "editorial assist clank",
    "quartermaster clank",
    "watch clank",
    "diagnostic-clank-honor-miss-evidence",
    "clankopsdashboard",
    "project anilwriter",
    "grand dau unification",
    "chudbox-local",
    "self-audit-curriculum",
}

NESTED_EXTRA = [
    Path(r"C:\Users\anil\Desktop\Quartermaster Clank\token-stats"),
    Path(r"C:\Users\anil\Desktop\Watch clank\watch-clank"),
]

KNOWN_FLEET_SLUGS = {
    "chinese-tech-wire",
    "korean-tech-wire",
    "oem-radar",
    "semiconductor-intelligence",
    "watch-clank",
    "smartphone-clank",
    "smartwatch-clank",
    "feature-phone-clank",
    "tablet-clank",
    "standards-clank",
    "free-game-tracker",
    "clank-architecture",
    "motherclank",
    "unified-clank-platform",
    "diagnostic-clank",
    "cvc-clank",
    "clank-systems-handbook",
    "clank-ledger",
    "clankops",
}

ALIAS_HINTS = {
    "chinese-tech-wire": ["ctw", "chinese tech wire"],
    "korean-tech-wire": ["ktw", "korean tech wire"],
    "semiconductor-intelligence": ["si", "semi-int", "semiconductor intelligence"],
    "free-game-tracker": ["newsroom", "fgt"],
    "oem-radar": ["oemradar"],
    "watch-clank": ["horology-clank", "watch clank"],
    "cvc-clank": ["cvc"],
    "standards-clank": ["standards"],
    "motherclank": ["mother-clank"],
    "unified-clank-platform": ["ucp"],
    "editorial-assist-clank": ["story-intelligence-clank", "story-clank"],
    "quartermaster-clank": ["quartermaster"],
}

README_NAMES = ("README.md", "readme.md", "README.rst")
CONFIG_NAMES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
)


def _read_excerpt(path: Path, limit: int = 1200) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return None


def _first_heading(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip()
    return None


def _github_identity(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url, re.I)
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}".lower()


def _slug_from_path(path: Path) -> str:
    name = path.name.strip().lower().replace(" ", "-")
    name = re.sub(r"[^a-z0-9.-]+", "-", name)
    return name.strip("-")


def default_scan_roots(extra: Iterable[str | Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in DEFAULT_ROOTS:
        if root.exists():
            roots.append(root)
    if extra:
        for item in extra:
            p = Path(item)
            if p.exists():
                roots.append(p)
    # unique preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = os.path.normcase(str(r.resolve())) if r.exists() else os.path.normcase(str(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def discover_candidate_paths(
    roots: Iterable[Path], *, include_nested_extras: bool = True
) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        if not path.exists() or not path.is_dir():
            return
        if path.name in SKIP_DIR_NAMES:
            return
        try:
            key = os.path.normcase(str(path.resolve()))
        except OSError:
            key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for root in roots:
        if not root.exists():
            continue
        add(root)
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        name_l = root.name.lower()
        # Primary Clanks root: every child is a candidate.
        if name_l == "clanks":
            for child in children:
                if child.is_dir() and not child.name.startswith("."):
                    add(child)
            continue
        if name_l == "desktop":
            for child in children:
                if child.is_dir() and child.name.lower() in DESKTOP_INTERESTING:
                    add(child)
            continue
        if name_l == "default project":
            for child in children:
                if child.is_dir() and not child.name.startswith("."):
                    add(child)
            continue
        if name_l == "dau-ecosystem":
            repos = root / "repos"
            if repos.is_dir():
                for child in repos.iterdir():
                    if child.is_dir():
                        add(child)
            add(root / "launcher")
            add(root / "ledger")
            continue
        # Generic: if the root itself looks like a project, keep it; also
        # inspect immediate children that are git repos or have README.
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / ".git").exists() or any((child / n).exists() for n in README_NAMES):
                add(child)

    if include_nested_extras:
        for extra in NESTED_EXTRA:
            add(extra)
    return found


def _metadata(path: Path) -> dict[str, Any]:
    readme_text = None
    readme_path = None
    for name in README_NAMES:
        candidate = path / name
        if candidate.exists():
            readme_path = str(candidate)
            readme_text = _read_excerpt(candidate)
            break
    configs = []
    config_excerpts: dict[str, list[str]] = {}
    for name in CONFIG_NAMES:
        candidate = path / name
        if candidate.exists():
            configs.append(name)
            excerpt = _read_excerpt(candidate, 500)
            if excerpt:
                config_excerpts[name] = excerpt.splitlines()[:20]
    return {
        "readme_path": readme_path,
        "readme_heading": _first_heading(readme_text),
        "readme_excerpt": (readme_text.splitlines()[:12] if readme_text else []),
        "config_files": configs,
        "config_excerpts": config_excerpts,
        "has_env_file": any((path / n).exists() for n in SECRET_FILENAMES),
    }


def classify_candidate(candidate: dict[str, Any]) -> tuple[str, list[str], str]:
    """Return (classification, evidence, confidence). Never invents Clanks."""
    evidence: list[str] = []
    path = Path(candidate.get("local_path") or "")
    name = path.name.lower() if path else ""
    slug = candidate.get("slug") or ""
    heading = (candidate.get("readme_heading") or "").lower()
    excerpt = " ".join(candidate.get("readme_excerpt") or []).lower()
    remote = candidate.get("canonical_remote") or candidate.get("remote")
    gh = _github_identity(remote)
    group = candidate.get("discovery_group") or ""
    git_error = candidate.get("git_error")
    is_git = candidate.get("is_git")

    if name.startswith("_") and group == "clanks_root":
        evidence.append("underscore-prefixed directory under Clanks root")
        return "SUPPORT_COMPONENT", evidence, "high"

    if name in {"diag_venv"} or name.endswith("_venv"):
        evidence.append("virtualenv directory name")
        return "NOT_A_CLANK", evidence, "high"

    if "token-stats" in slug or name == "token-stats":
        evidence.append("upstream token-stats checkout used by Quartermaster")
        if remote and "annihilater/token-stats" in (remote or "").lower():
            evidence.append("remote is Annihilater/token-stats, not a Clank repo")
        return "SUPPORT_COMPONENT", evidence, "high"

    if name in {"self-audit-curriculum"}:
        evidence.append("README describes human training, not a Clank")
        return "NOT_A_CLANK", evidence, "high"

    dau_names = {
        "chudbox",
        "compiler-workbench",
        "dau-practice-labs",
        "dau-world-generator",
        "fab-lab",
        "idle-time-learning-doodad",
        "ml-lab",
        "movement-bench",
        "os-lab",
        "packet-lab",
        "pipeline-playground",
        "dau-ecosystem",
    }
    if slug in dau_names or name in dau_names:
        evidence.append("Dead Air University lab/ecosystem artefact")
        return "NOT_A_CLANK", evidence, "high"

    if name in {"clankopsdashboard"} and not candidate.get("config_files") and not is_git:
        evidence.append("empty desktop placeholder directory")
        return "UNKNOWN", evidence, "medium"

    if name in {"anilwriter_work"} or "anilwriter" in name:
        evidence.append("AnilWriter-related tooling directory")
        return "PROBABLE", evidence, "low"

    if git_error and "dubious ownership" in (git_error or "").lower():
        evidence.append("git inspect blocked by dubious ownership")
        if "cvc" in heading or "cvc" in slug:
            evidence.append("README identifies CVC Clank")
            return "NEEDS_RECONSTRUCTION", evidence, "medium"

    if name in {"watch clank"} and not (path / "pyproject.toml").exists():
        evidence.append("desktop wrapper containing nested watch-clank checkout")
        return "SUPPORT_COMPONENT", evidence, "medium"

    if name in {"quartermaster clank"}:
        evidence.append("desktop Quartermaster wrapper (launcher + token-stats)")
        return "PROBABLE", evidence, "medium"

    if name in {"editorial assist clank", "cvc workbench"}:
        evidence.append("local project with README and pyproject, no git")
        if heading:
            evidence.append(f"README heading: {candidate.get('readme_heading')}")
        return "PROBABLE", evidence, "medium"

    if name in {"grand dau unification", "chudbox-local", "diagnostic-clank-honor-miss-evidence"}:
        evidence.append("desktop artefact without project metadata")
        return "UNKNOWN", evidence, "low"

    if name in {"clank base"}:
        evidence.append("nearly empty home directory named Clank Base")
        return "UNKNOWN", evidence, "low"

    if name in {"_launchers", "launchers"} or group == "launchers":
        evidence.append("launcher scripts directory")
        return "SUPPORT_COMPONENT", evidence, "high"

    fleet_hit = slug in KNOWN_FLEET_SLUGS or any(
        k in slug for k in ("-clank", "tech-wire", "oem-radar")
    )
    github_nbc = bool(gh and gh.startswith("anil-ganti-nbc/"))
    readme_clank = "clank" in heading or "clank" in excerpt[:400]
    if is_git and github_nbc and (fleet_hit or readme_clank or candidate.get("config_files")):
        evidence.append("git remote origin on anil-ganti-nbc")
        if candidate.get("readme_heading"):
            evidence.append(f"README heading: {candidate.get('readme_heading')}")
        if candidate.get("config_files"):
            evidence.append("project config: " + ", ".join(candidate["config_files"]))
        evidence.append("git inspect (read-only)")
        return "VERIFIED", evidence, "high"

    if is_git and github_nbc:
        evidence.append(f"github remote {gh}")
        return "PROBABLE", evidence, "medium"

    if is_git and (fleet_hit or readme_clank):
        evidence.append("local git repo with Clank-like naming or README")
        if git_error:
            return "NEEDS_RECONSTRUCTION", evidence, "medium"
        return "PROBABLE", evidence, "medium"

    if candidate.get("github_only"):
        repo = (candidate.get("github_repo") or "").lower()
        if repo.split("/")[-1] in KNOWN_FLEET_SLUGS or "clank" in repo:
            evidence.append("GitHub repository under anil-ganti-nbc")
            if candidate.get("github_description"):
                evidence.append("GitHub description")
            return "VERIFIED", evidence, "medium"
        if repo.split("/")[-1] in dau_names:
            evidence.append("GitHub DAU lab repository")
            return "NOT_A_CLANK", evidence, "high"
        evidence.append("GitHub repository without local checkout")
        return "UNKNOWN", evidence, "low"

    if not is_git and not candidate.get("readme_heading") and not candidate.get("config_files"):
        evidence.append("no git, README, or project config")
        return "UNKNOWN", evidence, "low"

    evidence.append("insufficient evidence to classify as a Clank")
    return "UNKNOWN", evidence, "low"


def inspect_path(path: Path, *, group: str) -> dict[str, Any]:
    meta = _metadata(path)
    git = inspect_git(path)
    slug = _slug_from_path(path)
    heading = meta.get("readme_heading")
    candidate: dict[str, Any] = {
        "name": slug,
        "slug": slug,
        "display_name": heading or path.name,
        "aliases": list(ALIAS_HINTS.get(slug, [])),
        "local_path": str(path),
        "discovery_group": group,
        "readme_heading": heading,
        "readme_excerpt": meta.get("readme_excerpt") or [],
        "config_files": meta.get("config_files") or [],
        "has_env_file": meta.get("has_env_file"),
        **git,
        "remote": git.get("canonical_remote"),
        "canonical_remote": git.get("canonical_remote"),
    }
    if heading and "story intelligence" in heading.lower():
        candidate["aliases"] = list(
            dict.fromkeys([*candidate["aliases"], "story-intelligence-clank", "story-clank"])
        )
        candidate["slug"] = "editorial-assist-clank"
        candidate["name"] = "editorial-assist-clank"
    if heading and heading.lower().startswith("cvc clank"):
        candidate["slug"] = "cvc-clank"
        candidate["name"] = "cvc-clank"
        candidate["aliases"] = list(dict.fromkeys([*candidate["aliases"], "cvc"]))
    if heading and heading.lower().startswith("cvc workbench"):
        candidate["slug"] = "cvc-workbench"
        candidate["name"] = "cvc-workbench"
    if heading and heading.lower().startswith("newsroom"):
        candidate["aliases"] = list(dict.fromkeys([*candidate["aliases"], "newsroom"]))
    classification, evidence, confidence = classify_candidate(candidate)
    candidate["classification"] = classification
    candidate["evidence"] = evidence
    candidate["confidence"] = confidence
    candidate["apparent_purpose"] = heading
    candidate["apparent_status"] = _infer_status(candidate)
    candidate["deployment_hints"] = _deployment_hints(path)
    candidate["relationships"] = []
    return candidate


def _infer_status(candidate: dict[str, Any]) -> str | None:
    text = " ".join(candidate.get("readme_excerpt") or []).lower()
    if "promotion frozen" in text or "unverified_production" in text:
        return "UNVERIFIED_PRODUCTION"
    if "experimental" in text:
        return "experimental"
    if "abandoned" in text:
        return "abandoned"
    if candidate.get("classification") == "SUPPORT_COMPONENT":
        return "support"
    return None


def _deployment_hints(path: Path) -> list[str]:
    hints: list[str] = []
    for rel in (
        "Dockerfile",
        "docker-compose.staging.yml",
        "docker-compose.yml",
        "deploy",
        "PHASE0_CONTAINMENT.md",
        "HANDOFF.md",
    ):
        if (path / rel).exists():
            hints.append(rel)
    return hints


def _group_for(path: Path) -> str:
    parts = [p.lower() for p in path.parts]
    if "clanks" in parts:
        if path.name.lower() == "clanks":
            return "clanks_root_dir"
        parent = path.parent.name.lower()
        if parent == "clanks":
            return "clanks_root"
        return "clanks_nested"
    if "desktop" in parts:
        return "desktop"
    if "default project" in parts:
        return "documents_nested"
    if "dau-ecosystem" in parts:
        return "dau_repos"
    return "other"


def github_repos(owner: str = "anil-ganti-nbc") -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            [
                "gh",
                "repo",
                "list",
                owner,
                "--limit",
                "200",
                "--json",
                "name,description,url,isArchived,isPrivate,updatedAt,defaultBranchRef",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []


def merge_github(
    candidates: list[dict[str, Any]], repos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for cand in candidates:
        gh = _github_identity(cand.get("canonical_remote") or cand.get("remote"))
        if gh:
            seen.add(gh)
        name = (cand.get("slug") or "").lower()
        if name:
            seen.add("anil-ganti-nbc/" + name)
    extra: list[dict[str, Any]] = []
    for repo in repos:
        ident = f"anil-ganti-nbc/{repo['name']}".lower()
        if ident in seen:
            continue
        default_branch = None
        ref = repo.get("defaultBranchRef")
        if isinstance(ref, dict):
            default_branch = ref.get("name")
        cand = {
            "name": repo["name"],
            "slug": repo["name"],
            "display_name": repo["name"],
            "aliases": list(ALIAS_HINTS.get(repo["name"], [])),
            "local_path": None,
            "discovery_group": "github",
            "github_only": True,
            "github_repo": ident,
            "github_description": repo.get("description") or None,
            "remote": repo.get("url"),
            "canonical_remote": repo.get("url"),
            "is_git": False,
            "default_branch": default_branch,
            "apparent_purpose": repo.get("description") or None,
            "apparent_status": "archived" if repo.get("isArchived") else None,
            "is_private": repo.get("isPrivate"),
            "github_updated_at": repo.get("updatedAt"),
            "readme_heading": None,
            "readme_excerpt": [],
            "config_files": [],
        }
        classification, evidence, confidence = classify_candidate(cand)
        cand["classification"] = classification
        cand["evidence"] = evidence
        cand["confidence"] = confidence
        extra.append(cand)
    return candidates + extra


def mark_secondary_checkouts(candidates: list[dict[str, Any]]) -> None:
    """If a Clanks-root checkout exists, other local copies become support components."""
    by_gh: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        gh = _github_identity(cand.get("canonical_remote") or cand.get("remote"))
        if gh:
            by_gh.setdefault(gh, []).append(cand)
    for group in by_gh.values():
        primaries = [c for c in group if c.get("discovery_group") == "clanks_root"]
        if not primaries:
            continue
        primary_path = primaries[0].get("local_path")
        for cand in group:
            if cand is primaries[0]:
                continue
            if cand.get("github_only"):
                continue
            if cand.get("discovery_group") == "clanks_root":
                continue
            cand["classification"] = "SUPPORT_COMPONENT"
            cand["confidence"] = "high"
            evidence = list(cand.get("evidence") or [])
            evidence.append(
                f"secondary local checkout; primary is {primary_path}"
            )
            cand["evidence"] = evidence
            cand["apparent_status"] = "duplicate_checkout"


def detect_duplicates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_remote: dict[str, list[str]] = {}
    for cand in candidates:
        remote = cand.get("canonical_remote") or cand.get("remote")
        gh = _github_identity(remote)
        key = gh or (redact_url(remote) if remote else None)
        if not key:
            continue
        by_remote.setdefault(key, []).append(cand.get("local_path") or cand.get("slug") or "")
    dupes = []
    for key, paths in by_remote.items():
        uniq = [p for p in dict.fromkeys(paths) if p]
        if len(uniq) > 1:
            dupes.append({"identity": key, "paths": uniq})
    return dupes


def run_census(
    *,
    roots: Iterable[str | Path] | None = None,
    include_github: bool = True,
    extra_paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    if roots is None:
        scan_roots = default_scan_roots()
        include_nested = True
    else:
        scan_roots = [Path(r) for r in roots if Path(r).exists()]
        include_nested = False
    paths = discover_candidate_paths(scan_roots, include_nested_extras=include_nested)
    if extra_paths:
        for item in extra_paths:
            p = Path(item)
            if p.exists():
                paths.append(p)
    candidates: list[dict[str, Any]] = []
    for path in paths:
        group = _group_for(path)
        try:
            candidates.append(inspect_path(path, group=group))
        except OSError as exc:
            candidates.append(
                {
                    "name": _slug_from_path(path),
                    "slug": _slug_from_path(path),
                    "local_path": str(path),
                    "classification": "UNKNOWN",
                    "evidence": [f"inspect failed: {exc}"],
                    "confidence": "low",
                    "discovery_group": group,
                }
            )
    mark_secondary_checkouts(candidates)
    github_error = None
    repos: list[dict[str, Any]] = []
    if include_github:
        repos = github_repos()
        if not repos:
            github_error = "gh repo list returned no data or failed"
        candidates = merge_github(candidates, repos)
    duplicates = detect_duplicates(candidates)
    counts: dict[str, int] = {}
    for cand in candidates:
        cls = cand.get("classification") or "UNKNOWN"
        counts[cls] = counts.get(cls, 0) + 1
    dirty = sum(1 for c in candidates if c.get("dirty"))
    github_backed = sum(
        1
        for c in candidates
        if _github_identity(c.get("canonical_remote") or c.get("remote"))
        or c.get("github_only")
    )
    local_only = sum(
        1
        for c in candidates
        if c.get("local_path")
        and not c.get("github_only")
        and not _github_identity(c.get("canonical_remote") or c.get("remote"))
    )
    return {
        "census_version": 1,
        "scanned_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "scanner": "clankops.census",
            "mode": "read-only",
            "notes": [
                "Bootstrap reconstruction, not live ClankOps history.",
                "Dirty repositories were observed, not cleaned.",
                "Secret files were detected by name only and never read.",
                "Facts in this artefact predate ClankOps and must be imported as RECONSTRUCTED.",
            ],
            "github_error": github_error,
        },
        "roots": [str(r) for r in scan_roots],
        "counts": {
            **counts,
            "total_candidates": len(candidates),
            "dirty_repositories": dirty,
            "github_backed": github_backed,
            "local_only": local_only,
            "duplicate_identity_groups": len(duplicates),
        },
        "duplicates": duplicates,
        "candidates": candidates,
    }


def write_census(census: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(census, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return out


def default_census_path() -> Path:
    """Prefer a census file in cwd, else the copy shipped with this checkout."""
    env = os.environ.get("CLANKOPS_CENSUS")
    if env:
        return Path(env)
    cwd = Path("data/bootstrap/clank_census.json")
    if cwd.is_file():
        return cwd
    return Path(__file__).resolve().parents[2] / "data" / "bootstrap" / "clank_census.json"


def load_census(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
