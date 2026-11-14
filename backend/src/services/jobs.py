import os, time, uuid
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import or_, select, update
from src.config.settings import REDIS_URL
from src.models import Dataset, Job, JobAttempt
from src.services.business import metadata
from src.services.audit import record

# A worker owns an attempt through (attempt_no, lease_token). The lease only decides
# who may take over; every write is fenced by those two values, so a slow executor
# that wakes up after a takeover or cancellation can never overwrite the new state.
LEASE_SECONDS = int(os.getenv('JOB_LEASE_SECONDS', '60'))
ACTIVE = ('queued', 'running')


def _session():
    # Imported lazily so DATABASE_URL can be set before the module is imported.
    from src.config.database import Session
    return Session()


def create(db, data, user):
    if not db.get(Dataset,data.dataset_id): raise HTTPException(404,'数据批次不存在')
    allowed={p['key']:p for p in metadata().get('parameters',[])}
    if set(data.params)-set(allowed): raise HTTPException(422,'包含未知分析参数')
    params={k:p['default'] for k,p in allowed.items()}
    params.update(data.params)
    for key,p in allowed.items():
        v=params[key]
        if p['type']=='number':
            try:
                v=float(v)
                if not (p.get('min',-1e9)<=v<=p.get('max',1e9)): raise ValueError()
                params[key]=v
            except (ValueError,TypeError): raise HTTPException(422, f'{p["label"]} 超出范围或非数值')
        elif str(v) not in [str(o['value']) for o in p.get('options',[])]:
            raise HTTPException(422,f'{p["label"]} 不是有效选项')
    now=time.time()
    j=Job(name=data.name, dataset_id=data.dataset_id, params=params, created_by=user.id, created_at=now,
          status='queued', attempt_no=1)
    db.add(j); db.flush()
    db.add(JobAttempt(job_id=j.id, attempt_no=1, status='queued', created_at=now))
    db.flush()
    return j


def enqueue(job_id):
    try:
        r=Redis.from_url(REDIS_URL); r.rpush('analysis:queue',job_id); r.close()
    except Exception:
        pass # SQL queued status is the durable fallback


def _attempt(db, job_id, attempt_no):
    return db.scalar(select(JobAttempt).where(JobAttempt.job_id==job_id, JobAttempt.attempt_no==attempt_no))


def claim(job_id, worker):
    """Atomically take ownership of the job's next valid attempt.

    Handles a queued job and a running job whose lease expired (takeover). Returns
    (attempt_no, token), or None when another executor already won the attempt.
    """
    now=time.time()
    with _session() as db:
        j=db.get(Job,job_id)
        if not j: return None
        attempt_no=j.attempt_no or 1
        token=uuid.uuid4().hex
        if j.status=='queued':
            res=db.execute(update(Job).where(Job.id==job_id, Job.status=='queued', Job.attempt_no==attempt_no)
                .values(status='running', lease_owner=worker, lease_token=token, lease_expires_at=now+LEASE_SECONDS,
                        finished_at=None, cancel_reason='').execution_options(synchronize_session=False))
            if res.rowcount!=1: db.rollback(); return None
            a=_attempt(db,job_id,attempt_no)
            if a is None:
                a=JobAttempt(job_id=job_id, attempt_no=attempt_no, created_at=now)
                db.add(a); db.flush()
            a.status='running'; a.worker=worker; a.lease_token=token
            a.started_at=a.started_at or now; a.finished_at=None; a.error=''; a.cancel_reason=''
            db.commit()
            return attempt_no, token
        if j.status=='running' and (j.lease_expires_at is None or j.lease_expires_at < now):
            new_no=attempt_no+1
            res=db.execute(update(Job).where(Job.id==job_id, Job.status=='running', Job.attempt_no==attempt_no,
                    or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now))
                .values(status='running', attempt_no=new_no, lease_owner=worker, lease_token=token,
                        lease_expires_at=now+LEASE_SECONDS, finished_at=None, cancel_reason='')
                .execution_options(synchronize_session=False))
            if res.rowcount!=1: db.rollback(); return None
            reason='执行器失联，租约超时，已自动接管'
            old=_attempt(db,job_id,attempt_no)
            if old is None:
                old=JobAttempt(job_id=job_id, attempt_no=attempt_no, created_at=now)
                db.add(old); db.flush()
            if old.status in ACTIVE:
                old.status='superseded'; old.finished_at=now; old.takeover_reason=reason
            db.add(JobAttempt(job_id=job_id, attempt_no=new_no, status='running', worker=worker, lease_token=token,
                              created_at=now, started_at=now, takeover_of=old.id, takeover_reason=reason))
            db.commit()
            return new_no, token
        return None


def finish(job_id, attempt_no, token, result=None, error=None):
    """Fenced completion. Returns False when the attempt is no longer authoritative."""
    now=time.time()
    status='failed' if error is not None else 'completed'
    with _session() as db:
        values=dict(status=status, finished_at=now, lease_owner='', lease_token='', lease_expires_at=None,
                    error=(error or '')[:990])
        if result is not None: values['result']=result
        res=db.execute(update(Job).where(Job.id==job_id, Job.status=='running', Job.attempt_no==attempt_no,
                Job.lease_token==token).values(**values).execution_options(synchronize_session=False))
        applied=res.rowcount==1
        attempt_filter=(JobAttempt.job_id==job_id, JobAttempt.attempt_no==attempt_no,
                        JobAttempt.status=='running', JobAttempt.lease_token==token)
        if applied:
            db.execute(update(JobAttempt).where(*attempt_filter)
                .values(status=status, finished_at=now, error=(error or '')[:990])
                .execution_options(synchronize_session=False))
            record(db,'worker','job.'+status,{'id':job_id,'attempt':attempt_no,'error':error or ''})
        else:
            db.execute(update(JobAttempt).where(*attempt_filter)
                .values(status='superseded', finished_at=now,
                        error='结果已丢弃：租约失效，任务已被接管或取消')
                .execution_options(synchronize_session=False))
            record(db,'worker','job.discarded',{'id':job_id,'attempt':attempt_no})
        db.commit()
        return applied


def renew(job_id, attempt_no, token):
    """Heartbeat for a long computation so other executors keep their hands off."""
    with _session() as db:
        res=db.execute(update(Job).where(Job.id==job_id, Job.status=='running', Job.attempt_no==attempt_no,
                Job.lease_token==token)
            .values(lease_expires_at=time.time()+LEASE_SECONDS).execution_options(synchronize_session=False))
        db.commit()
        return res.rowcount==1


def cancel(db, job, actor, reason=''):
    """Cancel a queued or running job. Conditional update makes concurrent cancels safe."""
    reason=(reason or '').strip()[:490] or '用户取消'
    if job.status not in ACTIVE:
        raise HTTPException(409,'仅排队中或计算中的任务可以取消')
    now=time.time()
    attempt_no=job.attempt_no or 1
    res=db.execute(update(Job).where(Job.id==job.id, Job.status.in_(ACTIVE), Job.attempt_no==attempt_no)
        .values(status='cancelled', cancel_reason=reason, finished_at=now, lease_owner='', lease_token='',
                lease_expires_at=None, approved=False, result={})
        .execution_options(synchronize_session=False))
    if res.rowcount!=1:
        db.rollback(); raise HTTPException(409,'任务状态已变化，请刷新后再试')
    db.execute(update(JobAttempt).where(JobAttempt.job_id==job.id, JobAttempt.attempt_no==attempt_no,
            JobAttempt.status.in_(ACTIVE))
        .values(status='cancelled', finished_at=now, cancel_reason=reason)
        .execution_options(synchronize_session=False))
    db.expire_all()
    record(db,actor,'job.cancel',{'id':job.id,'attempt':attempt_no,'reason':reason})
    db.commit()


def retry(db, job, actor):
    """Requeue a failed/cancelled job with its original parameters, exactly once.

    The conditional update is the idempotency gate: duplicate clicks can only move
    the job out of failed/cancelled a single time.
    """
    if job.status not in ('failed','cancelled'):
        raise HTTPException(409,'仅失败或已取消的任务可以重试')
    attempt_no=job.attempt_no or 1
    now=time.time()
    res=db.execute(update(Job).where(Job.id==job.id, Job.status.in_(('failed','cancelled')),
            Job.attempt_no==attempt_no)
        .values(status='queued', attempt_no=attempt_no+1, result={}, error='', approved=False, finished_at=None,
                cancel_reason='', lease_owner='', lease_token='', lease_expires_at=None)
        .execution_options(synchronize_session=False))
    if res.rowcount!=1:
        db.rollback(); raise HTTPException(409,'任务已重新排队或已完成，本次重试未生效')
    db.add(JobAttempt(job_id=job.id, attempt_no=attempt_no+1, status='queued', created_at=now))
    db.expire_all()
    record(db,actor,'job.retry',{'id':job.id,'attempt':attempt_no+1})
    db.commit()
    return attempt_no+1


def attempts_by_job(db, ids):
    if not ids: return {}
    result={}
    for a in db.scalars(select(JobAttempt).where(JobAttempt.job_id.in_(list(ids))
            ).order_by(JobAttempt.job_id, JobAttempt.attempt_no)):
        result.setdefault(a.job_id,[]).append(a)
    return result
