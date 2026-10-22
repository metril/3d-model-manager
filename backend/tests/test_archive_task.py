import pytest
from celery.exceptions import Retry
from sqlalchemy import select

from app.models import PrintArchive
from app.tasks.archive import archive_print

pytestmark = pytest.mark.usefixtures("printer_enabled", "data_dir")


def test_archive_task_retries_then_succeeds(fake_adapter, sliced_3mf_bytes, seed):
    job_id = seed.job(subtask="X_plate_1")
    fake_adapter.files["/cache/X_plate_1.3mf"] = sliced_3mf_bytes
    fake_adapter.download_errors = 2
    # Eager mode raises Retry instead of re-running; redeliver as a broker would.
    for _ in range(2):
        with pytest.raises(Retry):
            archive_print.apply(args=[job_id, "completed"])
    archive_print.apply(args=[job_id, "completed"])
    seed.s.expire_all()
    arc = seed.s.scalar(select(PrintArchive).where(PrintArchive.print_job_id == job_id))
    assert arc.fetch_state == "done" and len(fake_adapter.downloads) == 3
