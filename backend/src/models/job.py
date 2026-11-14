from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    # Each execution is an attempt. A job is claimed through (attempt_no, lease_token);
    # conditional updates on those two values guarantee a single valid executor.
    attempt_no: Mapped[int] = mapped_column(default=1)
    lease_owner: Mapped[str] = mapped_column(String(120), default='')
    lease_token: Mapped[str] = mapped_column(String(64), default='')
    lease_expires_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    cancel_reason: Mapped[str] = mapped_column(String(500), default='')


class JobAttempt(Base):
    __tablename__ = 'job_attempts'
    __table_args__ = (UniqueConstraint('job_id', 'attempt_no', name='uq_job_attempt'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    attempt_no: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default='queued')
    worker: Mapped[str] = mapped_column(String(120), default='')
    lease_token: Mapped[str] = mapped_column(String(64), default='')
    created_at: Mapped[float] = mapped_column(Double)
    started_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    error: Mapped[str] = mapped_column(String(1000), default='')
    cancel_reason: Mapped[str] = mapped_column(String(500), default='')
    takeover_of: Mapped[int | None] = mapped_column(nullable=True)
    takeover_reason: Mapped[str] = mapped_column(String(500), default='')
