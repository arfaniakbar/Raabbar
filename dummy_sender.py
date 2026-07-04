import os
import random
import requests

API_URL = "http://127.0.0.1:5000/api/trash"

medical_folder = "static/uploads/Medical"
nonmedical_folder = "static/uploads/Non Medical"

# pilih kategori acak
if random.random() < 0.5:
    kategori = "Medical"
    folder = medical_folder
    jenis = "Medical Waste"
else:
    kategori = "Non Medical"
    folder = nonmedical_folder
    jenis = "Cardboard"

# ambil semua gambar
images = [
    f for f in os.listdir(folder)
    if f.lower().endswith((".jpg", ".jpeg", ".png"))
]

if len(images) == 0:
    raise Exception(f"Tidak ada gambar pada folder {folder}")

filename = random.choice(images)
filepath = os.path.join(folder, filename)

print("Mengirim :", filepath)

with open(filepath, "rb") as img:

    files = {
        "image": img
    }

    data = {
        "kategori": kategori,
        "jenis_sampah": jenis,
        "confidence": round(random.uniform(0.90,0.99),2)
    }

    r = requests.post(
        API_URL,
        files=files,
        data=data
    )

print(r.status_code)
print(r.text)