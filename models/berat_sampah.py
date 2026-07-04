from database.db import db
from datetime import datetime

class BeratSampah(db.Model):

    __tablename__ = 'berat_sampah'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    id_pengambilan = db.Column(
        db.BigInteger,
        db.ForeignKey('pengambilan_sampah.id'),
        nullable=False
    )

    berat_total = db.Column(
        db.Numeric(6, 2),
        nullable=False
    )

    waktu_update = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )