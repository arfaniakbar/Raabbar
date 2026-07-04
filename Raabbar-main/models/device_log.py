from database.db import db
from datetime import datetime

class DeviceLog(db.Model):

    __tablename__ = 'device_logs'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    nama_perangkat = db.Column(
        db.String(100),
        nullable=False
    )

    status = db.Column(
        db.String(50),
        nullable=False
    )

    pesan = db.Column(
        db.Text
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )