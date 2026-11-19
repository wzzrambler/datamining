from pathlib import Path

from alembic import op

revision = '0001'
down_revision = None


def upgrade():
    for statement in Path(__file__).with_name('initial.sql').read_text('utf-8').split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    op.execute('DROP SCHEMA p7_app CASCADE')
    op.execute('DROP SCHEMA p6_kg CASCADE')
