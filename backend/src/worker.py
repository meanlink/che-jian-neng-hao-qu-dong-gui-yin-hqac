import os, socket, time, json, logging, threading
from pathlib import Path
from sqlalchemy import or_, select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report
from src.services.business import analyze
from src.services.jobs import LEASE_SECONDS, claim, finish, renew
from src.services.reports import deliver
from src.services.audit import record
logging.basicConfig(level=logging.INFO)

WORKER_NAME = socket.gethostname() + ':' + str(os.getpid())


def _keep_lease(job_id, attempt_no, token, stop):
    """Renew the lease while a long computation runs so peers do not take it over."""
    interval = max(2, LEASE_SECONDS // 3)
    while not stop.wait(interval):
        try:
            if not renew(job_id, attempt_no, token):
                logging.info('Lease lost for job %s attempt %s; abandoning result', job_id, attempt_no)
                return
        except Exception:
            logging.warning('Lease renewal failed for job %s', job_id)


def execute(job_id):
    handle = claim(job_id, WORKER_NAME)
    if not handle:
        return  # another executor owns this attempt
    attempt_no, token = handle
    stop = threading.Event()
    threading.Thread(target=_keep_lease, args=(job_id, attempt_no, token, stop), daemon=True).start()
    try:
        try:
            with Session() as db:
                j = db.get(Job, job_id)
                if not j:
                    raise RuntimeError('分析任务不存在')
                records = db.get(Dataset, j.dataset_id).records
                params = j.params
            result = analyze(records, params)
            json.dumps(result, allow_nan=False)  # reject non-serialisable results before writing
        except Exception as e:
            logging.exception('Analysis failed')
            finish(job_id, attempt_no, token, error=str(e))
            return
        applied = finish(job_id, attempt_no, token, result=result)
        if not applied:
            logging.info('Discarded stale result for job %s attempt %s', job_id, attempt_no)
    finally:
        stop.set()


def scan_durable_work(limit=5):
    """SQL is the durable source of truth, Redis is only an accelerator.

    Queued jobs (lost message, worker restart, enqueue failure) and attempts left
    'running' by an executor that crashed past its lease (or by a job created before
    leases existed) are picked up here. claim() decides whether this worker starts
    work, so a live executor with a valid lease is never duplicated.
    """
    now = time.time()
    with Session() as db:
        ids = list(db.scalars(select(Job.id).where(Job.status == 'queued').order_by(Job.id).limit(limit)))
        stale = list(db.scalars(select(Job.id).where(
            Job.status == 'running',
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now)
        ).order_by(Job.id).limit(limit)))
    for job_id in ids + stale:
        try:
            execute(job_id)
        except Exception:
            logging.exception('Durable scan failed for job %s', job_id)


def tick():
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled == True, Report.next_run <= time.time())):
            d = deliver(db, r)
            if d: record(db, 'worker', 'report.delivered', {'id': r.id, 'delivery': d.id})
        db.commit()


def main():
    r = Redis.from_url(REDIS_URL, socket_timeout=3)
    last_scan = 0.0
    scan_every = max(5, LEASE_SECONDS // 2)
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item = r.blpop('analysis:queue', timeout=1)
            if item: execute(int(item[1]))
            if time.time() - last_scan > scan_every:
                scan_durable_work(); last_scan = time.time()
            tick()
        except Exception:
            logging.exception('Worker retry'); time.sleep(2)


if __name__ == '__main__':
    main()
