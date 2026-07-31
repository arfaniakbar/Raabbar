from database.db import db
from datetime import datetime

class Sampah(db.Model):

    __tablename__ = 'sampah'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    id_berat = db.Column(
        db.BigInteger,
        db.ForeignKey('berat_sampah.id'),
        nullable=False
    )

    kategori = db.Column(
        db.String(20),
        nullable=False
    )

    jenis_sampah = db.Column(
        db.String(50),
        nullable=False
    )

    label_sampah = db.Column(
        db.String(250)
    )

    confidence = db.Column(
        db.Float
    )

    gambar_sampah = db.Column(
        db.String(255)
    )

    waktu_deteksi = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )