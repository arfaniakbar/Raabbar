from database.db import db
from datetime import datetime

class TrashLog(db.Model):

    __tablename__ = 'trash_logs'

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    sampah_id = db.Column(
        db.BigInteger
    )

    kategori = db.Column(
        db.String(50)
    )

    jenis_sampah = db.Column(
        db.String(50)
    )

    label_sampah = db.Column(
        db.String(255)
    )

    confidence = db.Column(
        db.Float
    )

    image_path = db.Column(
        db.String(255)
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )