from database.db import db
from datetime import datetime

class BinStatus(db.Model):

    __tablename__ = "bin_status"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    berat_medis = db.Column(
        db.Float,
        default=0
    )

    berat_non_medis = db.Column(
        db.Float,
        default=0
    )

    jarak_medis = db.Column(
        db.Float,
        default=0
    )

    jarak_non_medis = db.Column(
        db.Float,
        default=0
    )

    status_medis = db.Column(
        db.String(30),
        default="Kosong"
    )

    status_non_medis = db.Column(
        db.String(30),
        default="Kosong"
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )