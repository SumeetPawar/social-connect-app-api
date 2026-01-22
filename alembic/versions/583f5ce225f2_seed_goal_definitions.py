"""seed goal definitions

Revision ID: 583f5ce225f2
Revises: 582f5ce225f1
Create Date: 2026-01-19 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '583f5ce225f2'
down_revision: Union[str, None] = '582f5ce225f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Insert seed data for goal_definitions
    op.execute("""
    INSERT INTO goal_definitions (key, label, unit, value_type)
    VALUES 
        ('steps', 'Steps', 'steps', 'int'),
        ('water', 'Water', 'liters', 'float')
    ON CONFLICT (key) DO NOTHING;
    """)


def downgrade() -> None:
    # Remove seed data
    op.execute("DELETE FROM goal_definitions WHERE key IN ('steps', 'water');")
