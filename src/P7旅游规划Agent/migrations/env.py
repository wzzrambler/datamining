import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p7.db import database_url, setting

with create_engine(setting('P7_1_MIGRATION_URL') or database_url(), hide_parameters=True).connect() as conn:
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()
