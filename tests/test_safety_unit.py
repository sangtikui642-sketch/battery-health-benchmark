import json
from pathlib import Path

import pytest

from battery_health.safety import RepositorySafetyError, check_repository_safety


def test_safe_repository_writes_a_portable_pass_report(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "src" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 42\n", encoding="utf-8")
    report_path = tmp_path / "safety.json"

    result = check_repository_safety(
        repository_root=repository,
        report_path=report_path,
        tracked_files=[Path("src/module.py")],
    )

    report_text = result.report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["status"] == "passed"
    assert report["issues"] == []
    assert str(repository.resolve()) not in report_text


def test_tracked_path_cannot_escape_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(RepositorySafetyError, match="escapes"):
        check_repository_safety(
            repository_root=repository,
            report_path=tmp_path / "safety.json",
            tracked_files=[Path("../outside.txt")],
        )


def test_private_key_header_is_reported_without_file_content(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    key_path = repository / "config" / "private.txt"
    key_path.parent.mkdir(parents=True)
    header = "-----BEGIN " + "PRIVATE KEY-----"
    key_path.write_text(header + "\nsynthetic-test-value\n", encoding="utf-8")
    report_path = tmp_path / "safety.json"

    with pytest.raises(RepositorySafetyError) as captured:
        check_repository_safety(
            repository_root=repository,
            report_path=report_path,
            tracked_files=[Path("config/private.txt")],
        )

    report_text = report_path.read_text(encoding="utf-8")
    assert header not in str(captured.value)
    assert header not in report_text
    assert json.loads(report_text)["issues"] == [
        {"path": "config/private.txt", "issue_type": "credential"}
    ]


def test_raw_data_policy_is_path_and_suffix_specific(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    allowed = repository / "tests" / "fixtures" / "anonymous.csv"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("cell_id,cycle_index\ncell-001,1\n", encoding="utf-8")
    report_path = tmp_path / "safety.json"

    result = check_repository_safety(
        repository_root=repository,
        report_path=report_path,
        tracked_files=[Path("tests/fixtures/anonymous.csv")],
    )

    assert result.issues == ()
