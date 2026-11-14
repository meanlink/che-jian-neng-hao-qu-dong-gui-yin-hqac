from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models

# Columns added after the first release. create_all() does not alter existing tables,
# so an existing deployment is upgraded in place without losing analysis history.
NEW_JOB_COLUMNS = {
    'attempt_no': 'INTEGER NOT NULL DEFAULT 1',
    'lease_owner': "VARCHAR(120) NOT NULL DEFAULT ''",
    'lease_token': "VARCHAR(64) NOT NULL DEFAULT ''",
    'lease_expires_at': 'DOUBLE NULL',
    'cancel_reason': "VARCHAR(500) NOT NULL DEFAULT ''",
}


def _add_missing_columns():
    insp = inspect(engine)
    if 'jobs' not in insp.get_table_names():
        return
    existing = {c['name'] for c in insp.get_columns('jobs')}
    with engine.begin() as conn:
        for name, ddl in NEW_JOB_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE jobs ADD COLUMN {name} {ddl}'))


def migrate():
    Base.metadata.create_all(engine)
    _add_missing_columns()


if __name__ == '__main__':
    migrate()
