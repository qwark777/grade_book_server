"""Ensure the audit log exists in managed deployments."""

from alembic import op


revision = "20260628_0007"
down_revision = "20260628_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            school_id INT NULL,
            user_id INT NULL,
            action VARCHAR(100) NOT NULL,
            entity_type VARCHAR(50) NULL,
            entity_id INT NULL,
            details JSON NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            INDEX idx_audit_school_created (school_id, created_at),
            INDEX idx_audit_user_created (user_id, created_at)
        )
        """
    )


def downgrade() -> None:
    # audit_log predates Alembic in some installations and may contain history.
    pass
