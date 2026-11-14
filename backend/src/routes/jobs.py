from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from src.config.database import get_db
from src.models import Job
from src.schemas.job import JobInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import jobs
from src.services.audit import record
from src.utils.serialization import job_dict
router=APIRouter(prefix='/jobs')

class CancelInput(BaseModel):
    reason: str = Field(default='', max_length=490)

def _body(db, j):
    return job_dict(j,jobs.attempts_by_job(db,[j.id]).get(j.id,[]))

@router.get('')
def listing(db=Depends(get_db),u=Depends(current_user)):
    items=list(db.scalars(select(Job).order_by(Job.id.desc()).limit(100)))
    history=jobs.attempts_by_job(db,[j.id for j in items])
    return [job_dict(j,history.get(j.id,[])) for j in items]

@router.post('',status_code=201)
def create(data:JobInput,db=Depends(get_db),u=Depends(can_write)):
    j=jobs.create(db,data,u); record(db,u.email,'job.create',{'id':j.id,'params':j.params})
    db.commit(); jobs.enqueue(j.id); return _body(db,j)

@router.get('/{id}')
def detail(id:int,db=Depends(get_db),u=Depends(current_user)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    return _body(db,j)

@router.post('/{id}/cancel')
def cancel(id:int,data:CancelInput|None=None,db=Depends(get_db),u=Depends(can_write)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    jobs.cancel(db,j,u.email,(data.reason if data else ''))
    db.refresh(j)
    return _body(db,j)

@router.post('/{id}/retry')
def retry(id:int,db=Depends(get_db),u=Depends(can_write)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    jobs.retry(db,j,u.email)
    jobs.enqueue(j.id)
    db.refresh(j)
    return _body(db,j)

@router.post('/{id}/approve')
def approve(id:int,db=Depends(get_db),u=Depends(admin_only)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    if j.status!='completed':raise HTTPException(409,'仅已完成结果可以批准')
    if not j.approved:
        j.approved=True; record(db,u.email,'job.approve',{'id':id,'attempt':j.attempt_no}); db.commit()
    db.refresh(j)
    return _body(db,j)
