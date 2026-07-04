from database.db import db
from datetime import datetime

class PengambilanSampah(db.Model):

    __tablename__ = 'pengambilan_sampah'

    id = db.Column(
        db.BigInteger,
        primary_key=True
    )

    tanggal_penuh = db.Column(
        db.DateTime
    )

    tanggal_dikosongkan = db.Column(
        db.DateTime
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )