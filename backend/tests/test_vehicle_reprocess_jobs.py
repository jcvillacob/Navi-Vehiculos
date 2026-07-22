from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import vehicle_reprocess_jobs as jobs


def _job_row(**overrides):
    row = {
        "id": 9,
        "status": "queued",
        "identifiers": ["AAA111", "BBB222"],
        "scope": "all",
        "skip_geotab": False,
        "total_targets": 2,
        "processed_targets": 0,
        "current_identifier": "AAA111",
        "errors": [],
        "error_message": None,
        "created_by_user_id": 4,
        "created_at": datetime.now(timezone.utc),
        "started_at": None,
        "finished_at": None,
    }
    row.update(overrides)
    return row


def test_row_to_job_calculates_persisted_progress():
    job = jobs._row_to_job(_job_row(status="running", processed_targets=1))
    assert job.total_targets == 2
    assert job.processed_targets == 1
    assert job.progress_pct == 50.0
    assert job.current_identifier == "AAA111"


def test_run_job_persists_each_result_and_finishes(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch_row", lambda job_id: _job_row())
    monkeypatch.setattr(jobs, "_mark_running", lambda job_id: True)
    monkeypatch.setattr(
        jobs,
        "batch_lookup_vehicles_stream",
        lambda identifiers, **kwargs: iter([
            SimpleNamespace(status="ok"),
            SimpleNamespace(status="error"),
        ]),
    )
    progress = []
    monkeypatch.setattr(
        jobs,
        "_update_progress",
        lambda job_id, processed, current, errors: progress.append((processed, current, list(errors))) or True,
    )
    finished = []
    monkeypatch.setattr(jobs, "_mark_done", lambda job_id, errors: finished.append((job_id, list(errors))))

    jobs.run_job(9)

    assert progress == [(1, "BBB222", []), (2, None, ["BBB222"])]
    assert finished == [(9, ["BBB222"])]


def test_run_job_stops_when_cancelled(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch_row", lambda job_id: _job_row())
    monkeypatch.setattr(jobs, "_mark_running", lambda job_id: True)
    monkeypatch.setattr(
        jobs,
        "batch_lookup_vehicles_stream",
        lambda identifiers, **kwargs: iter([SimpleNamespace(status="ok"), SimpleNamespace(status="ok")]),
    )
    monkeypatch.setattr(jobs, "_update_progress", lambda *args, **kwargs: False)
    finished = []
    monkeypatch.setattr(jobs, "_mark_done", lambda *args: finished.append(args))

    jobs.run_job(9)

    assert finished == []
