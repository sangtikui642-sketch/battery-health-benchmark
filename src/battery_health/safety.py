"""Repository checks for credentials and disallowed raw battery data."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RAW_DATA_SUFFIXES = {
    ".csv",
    ".h5",
    ".hdf5",
    ".json",
    ".mat",
    ".parquet",
    ".tar",
    ".tsv",
    ".zip",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?im)^\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"\s*[:=]\s*['\"]?[^\s'\"#]{8,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
)


class RepositorySafetyError(ValueError):
    """Raised after disallowed repository content is reported without its value."""


@dataclass(frozen=True)
class SafetyIssue:
    """A portable file-level issue that never contains sensitive content."""

    path: str
    issue_type: str


@dataclass(frozen=True)
class SafetyCheckResult:
    """Result and report path for a completed repository scan."""

    report_path: Path
    issues: tuple[SafetyIssue, ...]


def _repository_files(repository_root: Path) -> tuple[Path, ...]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repository_root,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RepositorySafetyError("Unable to enumerate repository files") from error
    if completed.returncode != 0:
        raise RepositorySafetyError("Unable to enumerate repository files")
    decoded = completed.stdout.decode("utf-8", errors="strict")
    return tuple(Path(value) for value in decoded.split("\0") if value)


def _portable_tracked_paths(
    *, repository_root: Path, tracked_files: Iterable[Path]
) -> list[tuple[Path, str]]:
    resolved_root = repository_root.resolve()
    resolved: list[tuple[Path, str]] = []
    for supplied_path in tracked_files:
        if supplied_path.is_absolute():
            raise RepositorySafetyError("Tracked file entries must be repository-relative")
        candidate = (repository_root / supplied_path).resolve()
        try:
            relative = candidate.relative_to(resolved_root)
        except ValueError as error:
            raise RepositorySafetyError("Tracked file entry escapes the repository") from error
        if candidate.is_file():
            resolved.append((candidate, relative.as_posix()))
    return sorted(resolved, key=lambda item: item[1])


def _is_raw_battery_path(portable_path: str) -> bool:
    path = Path(portable_path)
    parts = tuple(part.lower() for part in path.parts)
    raw_location = "raw_data" in parts or any(
        parts[index : index + 2] == ("data", "raw") for index in range(len(parts) - 1)
    )
    return raw_location and path.suffix.lower() in RAW_DATA_SUFFIXES


def _contains_credential(path: Path) -> bool:
    if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2 * 1024 * 1024:
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(pattern.search(content) is not None for pattern in CREDENTIAL_PATTERNS)


def check_repository_safety(
    *,
    repository_root: Path,
    report_path: Path,
    tracked_files: Iterable[Path] | None = None,
) -> SafetyCheckResult:
    """Scan repository-relative files and report only paths and issue categories."""

    supplied_files = _repository_files(repository_root) if tracked_files is None else tracked_files
    files = _portable_tracked_paths(
        repository_root=repository_root,
        tracked_files=supplied_files,
    )
    issues: list[SafetyIssue] = []
    for local_path, portable_path in files:
        if _is_raw_battery_path(portable_path):
            issues.append(SafetyIssue(path=portable_path, issue_type="raw_battery_data"))
        if _contains_credential(local_path):
            issues.append(SafetyIssue(path=portable_path, issue_type="credential"))

    result = SafetyCheckResult(report_path=report_path, issues=tuple(issues))
    report = {
        "schema_version": 1,
        "status": "failed" if issues else "passed",
        "scanned_file_count": len(files),
        "issues": [{"path": issue.path, "issue_type": issue.issue_type} for issue in result.issues],
        "redaction_policy": "credential values are never written to reports or errors",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if issues:
        raise RepositorySafetyError(
            f"Repository safety check failed; inspect {report_path.name} for issue categories"
        )
    return result
