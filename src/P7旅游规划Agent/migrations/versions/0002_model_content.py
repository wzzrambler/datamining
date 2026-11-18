from alembic import op

revision = '0002'
down_revision = '0001'


def upgrade():
    op.execute('ALTER TABLE p7_app.message ADD COLUMN model_content text')


def downgrade():
    op.execute('ALTER TABLE p7_app.message DROP COLUMN model_content')
