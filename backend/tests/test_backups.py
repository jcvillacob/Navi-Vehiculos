from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from app.services import backup_service


def test_create_postgres_backup_writes_metadata_and_hides_password(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://backup_user:secret%40password@db:5432/navi_db",
    )

    captured: dict = {}

    def fake_run(command, *, env, **kwargs):
        captured["command"] = command
        captured["env"] = env
        output_path = Path(command[command.index("--file") + 1])
        output_path.write_bytes(b"valid postgres dump")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    record = backup_service.create_postgres_backup(trigger="manual")

    assert record["trigger"] == "manual"
    assert record["size_bytes"] == len(b"valid postgres dump")
    assert len(record["sha256"]) == 64
    assert (tmp_path / record["filename"]).exists()
    assert "secret%40password" not in captured["command"]
    assert captured["env"]["PGPASSWORD"] == "secret@password"


def test_list_backups_ignores_unrelated_files(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    (tmp_path / "navi_vehiculos_20260715_020000_daily.dump").write_bytes(b"dump")
    (tmp_path / "not-a-backup.txt").write_text("ignore", encoding="utf-8")

    backups = backup_service.list_backups()

    assert [item["filename"] for item in backups] == [
        "navi_vehiculos_20260715_020000_daily.dump"
    ]
