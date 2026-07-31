import os
import random

from models.sampah import Sampah
from models.berat_sampah import BeratSampah
from models.pengambilan import PengambilanSampah

from database.db import db
from models.trash_log import TrashLog



def generate_dummy():

    pengambilan = PengambilanSampah()

    db.session.add(pengambilan)
    db.session.commit()

    berat = BeratSampah(
        id_pengambilan=pengambilan.id,
        berat_total=0
    )

    db.session.add(berat)
    db.session.commit()
    
    

    medical_folder = r"data/Klasifikasi/Medical"
    non_medical_folder = r"data/Klasifikasi/Non Medical"

    medical_images = os.listdir(medical_folder)[:20]
    non_medical_images = os.listdir(non_medical_folder)[:20]

    

    for image in medical_images:

        sampah = Sampah(
            id_berat=berat.id,
            kategori="Medical",
            jenis_sampah="Medical Waste",
            label_sampah="Medical Waste",
            confidence=round(
                random.uniform(0.80, 0.99),
                2
            ),
            gambar_sampah=f"{medical_folder}/{image}"
        )

        db.session.add(sampah)

    for image in non_medical_images:

        sampah = Sampah(
            id_berat=berat.id,
            kategori="Non Medical",
            jenis_sampah="Cardboard",
            label_sampah="Cardboard",
            confidence=round(
                random.uniform(0.80, 0.99),
                2
            ),
            gambar_sampah=f"{non_medical_folder}/{image}"
        )

        db.session.add(sampah)

db.session.commit()

# ==========================================
# BUAT TRASH LOG DARI DATA SAMPAH
# ==========================================

all_sampah = Sampah.query.all()

for item in all_sampah:

    log = TrashLog(
        sampah_id=item.id,
        kategori=item.kategori,
        jenis_sampah=item.jenis_sampah,
        label_sampah=item.label_sampah,
        confidence=item.confidence,
        image_path=item.gambar_sampah
    )

    db.session.add(log)

db.session.commit()

print("Dummy data berhasil dibuat")
print("Trash logs berhasil dibuat") 