import datetime

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app.code_template import generate_code, PRINT_DEFAULT_TEMPLATE
from app.models.print_job import PrintJob
from app.models.spool import SpoolStatus
from app.settings_service import get_all as get_settings

_MAX_CODE_ATTEMPTS = 5


async def create_print_job(session, **job_kwargs) -> PrintJob:
    """Create and flush a PrintJob with a unique job_code.

    Sequence is derived from MAX(id)+1 (monotonic even after deletes, unlike a
    row COUNT) and retried in a SAVEPOINT on a unique-constraint collision so a
    concurrent request never aborts the whole enclosing transaction.
    """
    settings = await get_settings(session)
    template = settings.get("print.code_template", PRINT_DEFAULT_TEMPLATE)

    for attempt in range(_MAX_CODE_ATTEMPTS):
        max_id = await session.scalar(select(func.max(PrintJob.id))) or 0
        code = generate_code(template, product_id=0, line_id=0, seq=max_id + 1 + attempt)
        job = PrintJob(job_code=code, **job_kwargs)
        session.add(job)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            session.expunge(job)
            continue
        return job
    raise RuntimeError("Could not generate a unique print job code")


def deduct_filament(job: PrintJob) -> None:
    for line in job.lines:
        spool = line.spool
        if not spool:
            continue
        spool.remaining_weight_g = max(0.0, spool.remaining_weight_g - line.used_g)
        spool.last_weight_update_at = datetime.datetime.utcnow()
        spool.last_weight_update_source = "print-log"
        if spool.remaining_weight_g <= 0:
            spool.status = SpoolStatus.empty
        elif spool.fill_percent < 20:
            spool.status = SpoolStatus.almost_empty
        elif spool.status == SpoolStatus.new:
            spool.status = SpoolStatus.opened


def restore_filament(job: PrintJob) -> None:
    for line in job.lines:
        spool = line.spool
        if not spool:
            continue
        spool.remaining_weight_g = min(spool.initial_weight_g, spool.remaining_weight_g + line.used_g)
        spool.last_weight_update_at = datetime.datetime.utcnow()
        spool.last_weight_update_source = "print-log-reversal"
        if spool.remaining_weight_g <= 0:
            spool.status = SpoolStatus.empty
        elif spool.fill_percent < 20:
            spool.status = SpoolStatus.almost_empty
        elif spool.status in (SpoolStatus.empty, SpoolStatus.almost_empty):
            spool.status = SpoolStatus.opened


def find_insufficient_lines(job: PrintJob) -> list[str]:
    """Return spool codes that no longer have enough remaining weight for their
    line's used_g — checked again at the done-transition since multiple planned
    jobs can overbook the same spool between creation and completion."""
    insufficient = []
    for line in job.lines:
        spool = line.spool
        if spool and line.used_g > spool.remaining_weight_g:
            insufficient.append(spool.spool_code)
    return insufficient
