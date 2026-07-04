from database.db import db
from datetime import datetime

class HistoryLog(db.Model):

    __tablename__ = 'history_logs'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    event_type = db.Column(
        db.String(100),
        nullable=False
    )

    source = db.Column(
        db.String(50),
        nullable=False
    )

    description = db.Column(
        db.Text
    )

    reference_id = db.Column(
        db.BigInteger
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )