"""
TRASH BIN - IOT CLIENT INTEGRATED WITH WEBSITE API
======================================================================
"""

import cv2
import numpy as np
import tflite_runtime.interpreter as tflite
import os
import sys
import time
import subprocess
import threading
import statistics
from typing import List, Tuple

import RPi.GPIO as GPIO
from flask import Flask, jsonify
from flask_cors import CORS
import requests  # Ditambahkan untuk komunikasi ke Website Flask
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =====================================================================
# CONFIG SERVER & API URL
# =====================================================================
URL_WEBSITE_BASE = "https://wastescan.site"
API_TRASH = URL_WEBSITE_BASE + "/api/trash"
API_WEIGHT = URL_WEBSITE_BASE + "/api/weight"
API_CAPACITY = URL_WEBSITE_BASE + "/api/capacity"
API_DEVICE_STATUS = URL_WEBSITE_BASE + "/api/device/status"
API_DEVICE_CONTROL = URL_WEBSITE_BASE + "/api/device"

# =====================================================================
# GLOBAL HTTP SESSION (Untuk mencegah Connection Reset / Timeout)
# =====================================================================
api_session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
api_session.mount("http://", adapter)
api_session.mount("https://", adapter)

# =====================================================================
# 0. STATE GLOBAL (Thread-Safe)
# =====================================================================
state_lock = threading.Lock()

shared_state = {
    "classifier": {
        "state": "STANDBY",
        "label": "",
        "akurasi": 0.0,
        "prob_medical": 0.0,
        "prob_non_medical": 0.0,
        "motion_percent": 0.0,
        "fps": 0.0,
        "last_update": None,
    },
    "capacity": {
        "medis": {"jarak_cm": None, "status": "Unknown"},
        "non_medis": {"jarak_cm": None, "status": "Unknown"},
        "last_update": None,
    },
    "weight": {
        "medis": {"gram": 0.0},
        "non_medis": {"gram": 0.0},
        "last_update": None,
    },
    "errors": [],
}

nol_medis = 0.0
noise_medis = 0.0
nol_nonmedis = 0.0
noise_nonmedis = 0.0

def update_state(section: str, data: dict):
    with state_lock:
        shared_state[section].update(data)
        shared_state[section]["last_update"] = time.time()

# Fungsi Pengiriman HTTP POST Non-Blocking (Supaya sensor tidak tersendat)
def kirim_api_async(url: str, payload: dict, files: dict = None):
    def run():
        try:
            headers = {
                "User-Agent": "Raabbar-IoT-Client/1.0",
                "Accept": "application/json"
            }
            if files:
                response = api_session.post(url, data=payload, files=files, headers=headers, timeout=25.0)
            else:
                response = api_session.post(url, json=payload, headers=headers, timeout=20.0)

            if response.status_code not in [200, 201]:
                print(f"[API-WARN] Gagal mengirim ke {url}. Respon: {response.status_code}")
        except Exception as e:
            print(f"[API-ERROR] Terputus dari server website: {url} -> {e}")
            # Mengirimkan status ERROR jika terjadi masalah komunikasi
            if url != API_DEVICE_STATUS:
                pass
                # kirim_status_device("Sensor Kamera", "ERROR") # Nonaktifkan sementara agar tidak spam

    threading.Thread(target=run, daemon=True).start()

def kirim_status_device(device_name: str, status_str: str):
    payload = {"device": device_name, "status": status_str}
    kirim_api_async(API_DEVICE_STATUS, payload)

def log_error(msg: str):
    print(f"[ERROR] {msg}")
    with state_lock:
        shared_state["errors"].append({"time": time.time(), "message": msg})
        shared_state["errors"] = shared_state["errors"][-20:]

# =====================================================================
# EVENTS ENABLE/DISABLE SETIAP SENSOR (KONTROL DARI WEB)
# =====================================================================
recalibrate_flag = threading.Event()
shutdown_event = threading.Event()
raspberry_enabled = threading.Event()
raspberry_enabled.set()

# Semua sensor default = Menyala (Set)
camera_enabled = threading.Event()
camera_enabled.set()

ultrasonik_medis_enabled = threading.Event()
ultrasonik_medis_enabled.set()

ultrasonik_non_medis_enabled = threading.Event()
ultrasonik_non_medis_enabled.set()

loadcell_medis_enabled = threading.Event()
loadcell_medis_enabled.set()

loadcell_non_medis_enabled = threading.Event()
loadcell_non_medis_enabled.set()


# =====================================================================
# 1. KONFIGURASI PIN HARDWARE & TUNING SENSOR
# =====================================================================
SERVO_PIN = 14

TRIG_MEDIS = 24
ECHO_MEDIS = 25
TRIG_NON_MEDIS = 8
ECHO_NON_MEDIS = 7

MEDIS_DT  = 17
MEDIS_SCK = 27
MEDIS_FAKTOR = 218.3

NONMEDIS_DT  = 22
NONMEDIS_SCK = 23
NONMEDIS_FAKTOR = 218.3

SAMPEL_WARMUP  = 30
SAMPEL_TARE    = 80
SAMPEL_BACA    = 8
WINDOW_TAMPIL  = 3
SIGMA_FILTER   = 2.0

servo_pwm = None
posisi_sekarang = 7.5
servo_lock = threading.Lock()

def gerakkan_servo_halus(sudut_tujuan):
    global posisi_sekarang, servo_pwm
    if servo_pwm is None:
        return

    with servo_lock:
        if sudut_tujuan == "kiri":
            duty_target = 3.5
        elif sudut_tujuan == "kanan":
            duty_target = 11.5
        else:
            duty_target = 7.5

        if posisi_sekarang == duty_target:
            return

        langkah = 0.05 if duty_target > posisi_sekarang else -0.05
        sementara = posisi_sekarang

        while abs(duty_target - sementara) > 0.025:
            sementara += langkah
            servo_pwm.ChangeDutyCycle(sementara)
            time.sleep(0.015)

        servo_pwm.ChangeDutyCycle(duty_target)
        posisi_sekarang = duty_target
        time.sleep(0.2)
        servo_pwm.ChangeDutyCycle(0)

# =====================================================================
# 2. ALGORITMA CORE TIMBANGAN (HIGH ACCURACY FILTER)
# =====================================================================
def baca_raw(pin_dt: int, pin_sck: int) -> int:
    timeout = time.time() + 2.0
    while GPIO.input(pin_dt) == 1:
        if time.time() > timeout or shutdown_event.is_set():
            raise TimeoutError(f"HX711 tidak merespons! (DT={pin_dt})")
    count = 0
    for _ in range(24):
        GPIO.output(pin_sck, True)
        count <<= 1
        GPIO.output(pin_sck, False)
        if GPIO.input(pin_dt) == 0:
            count += 1
    GPIO.output(pin_sck, True)
    GPIO.output(pin_sck, False)
    if count & 0x800000:
        count -= 0x1000000
    return count

def ambil_sampel(pin_dt: int, pin_sck: int, jumlah: int, jeda_ms: float = 5.0) -> List[float]:
    hasil = []
    for _ in range(jumlah):
        if shutdown_event.is_set():
            break
        try:
            hasil.append(float(baca_raw(pin_dt, pin_sck)))
        except TimeoutError:
            pass
        time.sleep(jeda_ms / 1000.0)
    return hasil

def rata_bersih(data: List[float]) -> Tuple[float, float]:
    if len(data) <= 2:
        return statistics.mean(data) if data else 0.0, 0.0
    m = statistics.mean(data)
    s = statistics.stdev(data)
    bersih = [x for x in data if abs(x - m) <= SIGMA_FILTER * s] if s > 0 else data
    if not bersih:
        bersih = data
    hasil = statistics.mean(bersih)
    noise = statistics.stdev(bersih) if len(bersih) > 1 else s
    return hasil, noise

def warmup(pin_dt: int, pin_sck: int, label: str):
    print(f"   Warmup {label}", end="", flush=True)
    for _ in range(SAMPEL_WARMUP):
        try:
            baca_raw(pin_dt, pin_sck)
        except TimeoutError:
            pass
        print(".", end="", flush=True)
        time.sleep(0.05)
    print(" OK")

def auto_tare(pin_dt: int, pin_sck: int, label: str) -> Tuple[float, float]:
    print(f"   Tare {label}...", end="", flush=True)
    data = ambil_sampel(pin_dt, pin_sck, SAMPEL_TARE, jeda_ms=10)
    nilai, noise = rata_bersih(data)
    print(f" OK (raw={nilai:.0f}, noise=±{noise:.0f})")
    return nilai, noise

# =====================================================================
# 3. THREAD AI KAMERA
# =====================================================================
def thread_kamera():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    MODEL_PATH = os.path.join(BASE_DIR, "model_rabbar.tflite")

    print("--- [KAMERA] Memuat Model TensorFlow Lite ---")
    try:
        interpreter = tflite.Interpreter(model_path=MODEL_PATH)
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
    except Exception as e:
        log_error(f"[KAMERA] Gagal memuat model: {e}")
        return

    RAW_FRAME_PATH = "/dev/shm/live_frame.jpg"
    cmd = [
        "libcamera-still", "-t", "0", "--timelapse", "40",
        "--width", "400", "--height", "300",
        "-o", RAW_FRAME_PATH, "-n", "--immediate",
    ]

    try:
        subprocess.run(["sudo", "killall", "-9", "libcamera-vid", "libcamera-still"], stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        if os.path.exists(RAW_FRAME_PATH):
            subprocess.run(["sudo", "rm", "-f", RAW_FRAME_PATH])
        camera_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[KAMERA] Menyiapkan sensor kamera...")
        time.sleep(2.0)
    except Exception as e:
        log_error(f"[KAMERA] Gagal mengaktifkan kamera: {e}")
        return

    MOTION_THRESHOLD = 25
    MOTION_MIN_AREA = 0.02
    KALIBRASI_FRAME = 20
    THRESHOLD_AKURASI = 50.0

    def baca_frame_aman():
        if not camera_enabled.is_set():
            return None
        for _ in range(5):
            try:
                if os.path.exists(RAW_FRAME_PATH) and os.path.getsize(RAW_FRAME_PATH) > 5000:
                    with open(RAW_FRAME_PATH, "rb") as f:
                        data = f.read()
                    if data[-2:] == b'\xff\xd9':
                        array = np.frombuffer(data, dtype=np.uint8)
                        frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
                        if frame is not None and frame.size > 0:
                            return frame
            except Exception:
                pass
            time.sleep(0.01)
        return None

    def ada_gerakan(frame, background):
        gray_now = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_now = cv2.GaussianBlur(gray_now, (21, 21), 0)
        diff = cv2.absdiff(background, gray_now)
        _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)
        total_pixel = thresh.size
        pixel_berubah = cv2.countNonZero(thresh)
        persen_area = pixel_berubah / total_pixel
        return persen_area >= MOTION_MIN_AREA, persen_area, thresh

    def klasifikasi(frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (224, 224))
        data = np.expand_dims(resized, axis=0).astype(np.float32) / 255.0
        interpreter.set_tensor(input_details[0]["index"], data)
        interpreter.invoke()
        pred = interpreter.get_tensor(output_details[0]["index"])[0]
        if len(pred) > 1:
            prob_M = pred[0] * 100
            prob_NM = pred[1] * 100
        else:
            prob_NM = pred[0] * 100
            prob_M = (1.0 - pred[0]) * 100
        if prob_NM > prob_M:
            return "Non Medical", prob_NM, prob_M, prob_NM
        return "Medical", prob_M, prob_M, prob_NM

    print("--- [KAMERA] Kalibrasi Background ---")
    background = None
    jumlah_frame_kalibrasi = 0
    while jumlah_frame_kalibrasi < KALIBRASI_FRAME:
        frame = baca_frame_aman()
        if frame is None:
            time.sleep(0.05)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if background is None:
            background = gray.astype(np.float32)
        else:
            cv2.accumulateWeighted(gray, background, 0.5)
        jumlah_frame_kalibrasi += 1
    background_ref = cv2.convertScaleAbs(background)
    print("[KAMERA] Kalibrasi selesai! Sensor siap.\n")

    STATE = "STANDBY"
    objek_terkunci = None
    waktu_mulai_kunci = None
    last_frame_time = 0
    keputusan_final = ""
    confidence_final = 0.0

    try:
        while not shutdown_event.is_set():
            if not raspberry_enabled.is_set() or not camera_enabled.is_set():
                time.sleep(0.5)
                continue

            if recalibrate_flag.is_set():
                frame_recal = baca_frame_aman()
                if frame_recal is not None:
                    gray_recal = cv2.cvtColor(frame_recal, cv2.COLOR_BGR2GRAY)
                    gray_recal = cv2.GaussianBlur(gray_recal, (21, 21), 0)
                    background = gray_recal.astype(np.float32)
                    background_ref = cv2.convertScaleAbs(background)
                    STATE = "STANDBY"
                    objek_terkunci = None
                    waktu_mulai_kunci = None
                recalibrate_flag.clear()

            if camera_process.poll() is not None:
                camera_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
                continue

            frame = baca_frame_aman()
            if frame is None:
                continue

            ada_objek, persen_area, frame_diff = ada_gerakan(frame, background_ref)
            label_teks = ""
            warna_teks = (255, 255, 255)
            status_countdown = ""
            prob_M = prob_NM = 0.0
            akurasi_tampil = 0.0
            prediksi_sekarang = ""
            class_idx = -1

            if STATE == "STANDBY":
                status_countdown = "STANDBY - Menunggu sampah..."
                gray_now = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_now = cv2.GaussianBlur(gray_now, (21, 21), 0)
                cv2.accumulateWeighted(gray_now, background, 0.01)
                background_ref = cv2.convertScaleAbs(background)

                if ada_objek:
                    STATE = "MENGUNCI"
                    objek_terkunci = None
                    waktu_mulai_kunci = None

            elif STATE == "MENGUNCI":
                if not ada_objek:
                    STATE = "STANDBY"
                    objek_terkunci = None
                    waktu_mulai_kunci = None
                else:
                    prediksi_sekarang, akurasi_tampil, prob_M, prob_NM = klasifikasi(frame)
                    class_idx = 0 if prediksi_sekarang == 'Medical' else 1

                    if akurasi_tampil <= THRESHOLD_AKURASI:
                        status_countdown = f"Menganalisa... ({akurasi_tampil:.1f}%)"
                        objek_terkunci = None
                        waktu_mulai_kunci = None
                    else:
                        if objek_terkunci != prediksi_sekarang:
                            objek_terkunci = prediksi_sekarang
                            waktu_mulai_kunci = time.time()

                        durasi = time.time() - waktu_mulai_kunci
                        if durasi < 1.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 1/3"
                        elif durasi < 2.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 2/3"
                        elif durasi < 3.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 3/3"
                        else:
                            keputusan_final = objek_terkunci
                            confidence_final = akurasi_tampil
                            STATE = "MEMBUANG"

                label_teks = f"{prediksi_sekarang} ({akurasi_tampil:.1f}%)" if akurasi_tampil > 0 else ""
                warna_teks = (0, 0, 255) if class_idx == 0 else (0, 255, 0)

            elif STATE == "MEMBUANG":
                status_countdown = f"MEMBUANG ({keputusan_final.upper()}) - Lock Model..."
                cv2.putText(frame, "LISTRIK DROP DIABAIKAN", (15, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

                # =============================================================
                # ① & ⑥ EKSEKUSI PENGIRIMAN DATA DETEKSI SAMPAH + GAMBAR KE WEBSITE
                # =============================================================
                nama_foto = f"trash_{int(time.time())}.jpg"
                _, img_encoded = cv2.imencode('.jpg', frame)

                payload_trash = {
                    "kategori": "Medical" if keputusan_final == "Medical" else "Non Medical",
                    "jenis_sampah": keputusan_final,
                    "confidence": round(float(confidence_final), 2)
                }
                files_trash = {'image': (nama_foto, img_encoded.tobytes(), 'image/jpeg')}

                print(f"[API] Mengirim Data Sampah Baru: {keputusan_final} ({confidence_final}%)")
                kirim_api_async(API_TRASH, payload_trash, files=files_trash)
                # =============================================================

                if keputusan_final == "Medical":
                    gerakkan_servo_halus("kiri")
                else:
                    gerakkan_servo_halus("kanan")
                time.sleep(2.5)

                gerakkan_servo_halus("tengah")
                time.sleep(1.2)
                STATE = "KEMBALI"

            elif STATE == "KEMBALI":
                status_countdown = "Menstabilkan sensor pasca-gerak..."
                gray_now = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_now = cv2.GaussianBlur(gray_now, (21, 21), 0)
                background = gray_now.astype(np.float32)
                background_ref = cv2.convertScaleAbs(background)

                STATE = "STANDBY"
                objek_terkunci = None
                waktu_mulai_kunci = None
                keputusan_final = ""

            warna_border = {"STANDBY": (100, 100, 100), "MENGUNCI": (0, 200, 255), "MEMBUANG": (0, 80, 255), "KEMBALI": (100, 255, 100)}.get(STATE, (255, 255, 255))
            cv2.rectangle(frame, (0, 0), (frame.shape[1]-1, frame.shape[0]-1), warna_border, 3)

            if label_teks:
                cv2.putText(frame, label_teks, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, warna_teks, 2)
            cv2.putText(frame, status_countdown, (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
            cv2.putText(frame, f"STATE: {STATE}  |  Motion: {persen_area*100:.1f}%", (15, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.45, warna_border, 1)

            fps = 1.0 / (time.time() - last_frame_time) if last_frame_time != 0 else 0
            last_frame_time = time.time()
            cv2.putText(frame, f"FPS: {fps:.1f}", (15, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            cv2.imshow('Deteksi Sampah Real-time', frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('r'):
                recalibrate_flag.set()

            update_state("classifier", {
                "state": STATE,
                "label": keputusan_final or prediksi_sekarang or status_countdown,
                "akurasi": round(float(akurasi_tampil), 1),
                "prob_medical": round(float(prob_M), 1),
                "prob_non_medical": round(float(prob_NM), 1),
                "motion_percent": round(float(persen_area) * 100, 1),
                "fps": round(float(fps), 1),
            })

    except Exception as e:
        log_error(f"[KAMERA] Gangguan: {e}")
    finally:
        cv2.destroyAllWindows()
        try:
            camera_process.terminate()
        except Exception:
            pass

# =====================================================================
# 4. THREAD DUAL ULTRASONIC (DIATUR CETAK & KIRIM TIAP 3 DETIK)
# =====================================================================
def thread_ultrasonic():
    def ambil_jarak(pin_trig, pin_echo):
        if shutdown_event.is_set():
            return None
        GPIO.output(pin_trig, False)
        time.sleep(0.02)
        GPIO.output(pin_trig, True)
        time.sleep(0.00001)
        GPIO.output(pin_trig, False)

        timeout_limit = time.time() + 0.1
        pancaran_mulai = time.time()
        while GPIO.input(pin_echo) == 0:
            pancaran_mulai = time.time()
            if pancaran_mulai > timeout_limit:
                return None

        timeout_limit = time.time() + 0.1
        pancaran_selesai = time.time()
        while GPIO.input(pin_echo) == 1:
            pancaran_selesai = time.time()
            if pancaran_selesai > timeout_limit:
                return None

        return round(((pancaran_selesai - pancaran_mulai) * 34300) / 2, 1)

    def ambil_jarak_stabil(pin_trig, pin_echo):
        sampel = []
        for _ in range(5): # Ambil 5 sampel cepat
            j = ambil_jarak(pin_trig, pin_echo)
            if j is not None and j > 0 and j < 400: # Batas logis HC-SR04
                sampel.append(j)
            time.sleep(0.05)
        if not sampel:
            return None
        return round(statistics.median(sampel), 1)

    def cek_kapasitas(jarak):
        if jarak is None: return "Error"
        if jarak <= 30: return "Penuh"
        if jarak <= 50: return "Hampir Penuh"
        return "Kosong"

    last_stat_um = "ON"
    last_stat_un = "ON"

    while not shutdown_event.is_set():
        if not raspberry_enabled.is_set():
            time.sleep(1.0)
            continue
            
        try:
            j_medis = None
            j_non_medis = None
            stat_medis = "OFF"
            stat_non_medis = "OFF"

            # Cek status on/off masing-masing ultrasonik
            if ultrasonik_medis_enabled.is_set():
                j_medis = ambil_jarak_stabil(TRIG_MEDIS, ECHO_MEDIS)
                stat_medis = cek_kapasitas(j_medis)
                
                cur_stat_um = "ERROR" if stat_medis == "Error" else "ON"
                if cur_stat_um != last_stat_um:
                    kirim_status_device("Sensor Ultrasonik Medis", cur_stat_um)
                    last_stat_um = cur_stat_um

            if ultrasonik_non_medis_enabled.is_set():
                j_non_medis = ambil_jarak_stabil(TRIG_NON_MEDIS, ECHO_NON_MEDIS)
                stat_non_medis = cek_kapasitas(j_non_medis)
                
                cur_stat_un = "ERROR" if stat_non_medis == "Error" else "ON"
                if cur_stat_un != last_stat_un:
                    kirim_status_device("Sensor Ultrasonik Non-Medis", cur_stat_un)
                    last_stat_un = cur_stat_un

            update_state("capacity", {
                "medis": {"jarak_cm": j_medis, "status": stat_medis},
                "non_medis": {"jarak_cm": j_non_medis, "status": stat_non_medis},
            })
            
            print(f"[ULTRASONIC] Medis: {j_medis if j_medis else 'OFF'} cm ({stat_medis}) | Non-Medis: {j_non_medis if j_non_medis else 'OFF'} cm ({stat_non_medis})")

            # =========================================================
            # ③ EKSEKUSI POST KAPASITAS KE WEBSITE
            # =========================================================
            payload_capacity = {
                "medis": {"jarak": j_medis if j_medis else 0, "status": stat_medis},
                "non_medis": {"jarak": j_non_medis if j_non_medis else 0, "status": stat_non_medis}
            }
            kirim_api_async(API_CAPACITY, payload_capacity)
            # =========================================================

        except Exception as e:
            log_error(f"[ULTRASONIC] Loop error: {e}")
        time.sleep(3.0)

def format_berat(gram: float) -> str:
    if gram >= 1000:
        return f"{gram / 1000:.2f} kg"
    return f"{gram:.1f} g"

# =====================================================================
# 5. THREAD DUAL TIMBANGAN (MODIFIKASI: DUKUNG TERMINAL KG / GRAM)
# =====================================================================
def thread_timbangan():
    global nol_medis, noise_medis, nol_nonmedis, noise_nonmedis

    # Batasi dead-zone maksimal 2.5 gram agar tetap sangat sensitif terhadap barang ringan
    dz_medis    = min(2.5, max(0.5, (noise_medis    / MEDIS_FAKTOR)    * 2.0))
    dz_nonmedis = min(2.5, max(0.5, (noise_nonmedis / NONMEDIS_FAKTOR) * 2.0))

    hist_medis: List[float] = []
    hist_nonmedis: List[float] = []

    berat_m_prev = 0.0
    berat_n_prev = 0.0
    
    last_stat_lm = "ON"
    last_stat_ln = "ON"

    while not shutdown_event.is_set():
        if not raspberry_enabled.is_set():
            time.sleep(1.0)
            continue
            
        try:
            tampil_m = 0.0
            tampil_n = 0.0

            if loadcell_medis_enabled.is_set():
                data_m = ambil_sampel(MEDIS_DT, MEDIS_SCK, SAMPEL_BACA, jeda_ms=5)
                
                cur_stat_lm = "ON" if data_m else "ERROR"
                if cur_stat_lm != last_stat_lm:
                    kirim_status_device("Sensor Load Cell Medis", cur_stat_lm)
                    last_stat_lm = cur_stat_lm
                    
                if data_m:
                    raw_m, _ = rata_bersih(data_m)
                    berat_m = (raw_m - nol_medis) / MEDIS_FAKTOR
                    if abs(berat_m) < dz_medis:
                        berat_m = 0.0

                    if abs(berat_m - berat_m_prev) > 20:
                        hist_medis.clear()
                    berat_m_prev = berat_m

                    hist_medis.append(berat_m)
                    if len(hist_medis) > 2: # Kurangi smoothing lag
                        hist_medis.pop(0)

                    tampil_m = max(0.0, round(statistics.mean(hist_medis), 1))

            if loadcell_non_medis_enabled.is_set():
                data_n = ambil_sampel(NONMEDIS_DT, NONMEDIS_SCK, SAMPEL_BACA, jeda_ms=5)
                
                cur_stat_ln = "ON" if data_n else "ERROR"
                if cur_stat_ln != last_stat_ln:
                    kirim_status_device("Sensor Load Cell Non-Medis", cur_stat_ln)
                    last_stat_ln = cur_stat_ln
                    
                if data_n:
                    raw_n, _ = rata_bersih(data_n)
                    berat_n = (raw_n - nol_nonmedis) / NONMEDIS_FAKTOR
                    if abs(berat_n) < dz_nonmedis:
                        berat_n = 0.0

                    if abs(berat_n - berat_n_prev) > 20:
                        hist_nonmedis.clear()
                    berat_n_prev = berat_n

                    hist_nonmedis.append(berat_n)
                    if len(hist_nonmedis) > 2: # Kurangi smoothing lag
                        hist_nonmedis.pop(0)

                    tampil_n = max(0.0, round(statistics.mean(hist_nonmedis), 1))

            update_state("weight", {
                "medis": {"gram": tampil_m},
                "non_medis": {"gram": tampil_n},
            })

            print(f"[TIMBANGAN] Medis: {format_berat(tampil_m) if loadcell_medis_enabled.is_set() else 'OFF'} | Non-Medis: {format_berat(tampil_n) if loadcell_non_medis_enabled.is_set() else 'OFF'}")

            # =========================================================
            # ② EKSEKUSI POST BERAT KE WEBSITE
            # =========================================================
            payload_weight = {
                "medis": tampil_m,
                "non_medis": tampil_n
            }
            kirim_api_async(API_WEIGHT, payload_weight)
            # =========================================================

        except Exception as e:
            log_error(f"[TIMBANGAN] Loop error: {e}")
        
        # Dikembalikan ke 1.5 detik agar server VPS tidak kewalahan dan menyebabkan timeout!
        time.sleep(1.5)

# =====================================================================
# ⑤ THREAD DEVICE CONTROL (GET DARI WEBSITE SECARA BERKALA)
# =====================================================================
def thread_device_control_poller():
    print("[SYSTEM] Thread Poller ON/OFF Kontrol Device Aktif.")
    headers = {
        "User-Agent": "Raabbar-IoT-Client/1.0",
        "Accept": "application/json"
    }
    while not shutdown_event.is_set():
        try:
            response = api_session.get(API_DEVICE_CONTROL, headers=headers, timeout=20.0)
            if response.status_code == 200:
                data = response.json()
                
                # Mengambil list devices dengan aman
                devices = data.get("devices", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                
                for dev in devices:
                    nama_perangkat = dev.get("device")
                    status_on = (dev.get("status", "ON").upper() == "ON")
                    
                    if nama_perangkat == "Sensor Kamera":
                        if status_on and not camera_enabled.is_set():
                            camera_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Kamera.")
                        elif not status_on and camera_enabled.is_set():
                            camera_enabled.clear()
                            print("[POLLER] MEMATIKAN Sensor Kamera.")
                            
                    elif nama_perangkat == "Sensor Ultrasonik Medis":
                        if status_on and not ultrasonik_medis_enabled.is_set():
                            ultrasonik_medis_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Ultrasonik Medis.")
                        elif not status_on and ultrasonik_medis_enabled.is_set():
                            ultrasonik_medis_enabled.clear()
                            print("[POLLER] MEMATIKAN Sensor Ultrasonik Medis.")
                            
                    elif nama_perangkat == "Sensor Ultrasonik Non-Medis":
                        if status_on and not ultrasonik_non_medis_enabled.is_set():
                            ultrasonik_non_medis_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Ultrasonik Non-Medis.")
                        elif not status_on and ultrasonik_non_medis_enabled.is_set():
                            ultrasonik_non_medis_enabled.clear()
                            print("[POLLER] MEMATIKAN Sensor Ultrasonik Non-Medis.")
                            
                    elif nama_perangkat == "Sensor Load Cell Medis":
                        if status_on and not loadcell_medis_enabled.is_set():
                            loadcell_medis_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Load Cell Medis.")
                        elif not status_on and loadcell_medis_enabled.is_set():
                            loadcell_medis_enabled.clear()
                            print("[POLLER] MEMATIKAN Sensor Load Cell Medis.")
                            
                    elif nama_perangkat == "Sensor Load Cell Non-Medis":
                        if status_on and not loadcell_non_medis_enabled.is_set():
                            loadcell_non_medis_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Load Cell Non-Medis.")
                        elif not status_on and loadcell_non_medis_enabled.is_set():
                            loadcell_non_medis_enabled.clear()
                            print("[POLLER] MEMATIKAN Sensor Load Cell Non-Medis.")
                            
                    elif nama_perangkat == "Sensor Raspberry Pi":
                        if status_on and not raspberry_enabled.is_set():
                            raspberry_enabled.set()
                            print("[POLLER] MENGHIDUPKAN Sensor Raspberry Pi.")
                        elif not status_on and raspberry_enabled.is_set():
                            raspberry_enabled.clear()
                            print("[POLLER] Website memerintahkan Raspberry Pi STANDBY (Jeda).")
                            
        except Exception as e:
            print(f"[POLLER-ERROR] Gagal membaca status kontrol dari Website: {e}")
        time.sleep(2.0)

# =====================================================================
# 6. FLASK API SERVER LOCAL (Bawaan Raspberry Pi kamu tetap aman)
# =====================================================================
app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def home():
    with state_lock: return jsonify(shared_state)

@app.route("/api/status", methods=["GET"])
def status():
    with state_lock: return jsonify(shared_state)

@app.route("/api/recalibrate", methods=["POST", "GET"])
def api_recalibrate():
    recalibrate_flag.set()
    return jsonify({"status": "ok", "message": "Kalibrasi ulang dipicu."})

def jalankan_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)

# =====================================================================
# 7. EXECUTOR UTAMA & PROSES PRO-TARE
# =====================================================================
def main():
    global nol_medis, noise_medis, nol_nonmedis, noise_nonmedis

    print("\n╔══════════════════════════════════════════════════════╗")
    print("║      SISTEM INTEGRASI TRASH BIN - CALIBRATING        ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    print("[SYSTEM] Pastikan KEDUA wadah timbangan KOSONG untuk zero-tracking!\n")

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    GPIO.setup(SERVO_PIN, GPIO.OUT)
    GPIO.setup(TRIG_MEDIS, GPIO.OUT)
    GPIO.setup(ECHO_MEDIS, GPIO.IN)
    GPIO.setup(TRIG_NON_MEDIS, GPIO.OUT)
    GPIO.setup(ECHO_NON_MEDIS, GPIO.IN)

    GPIO.setup(MEDIS_SCK, GPIO.OUT)
    GPIO.setup(MEDIS_DT, GPIO.IN)
    GPIO.setup(NONMEDIS_SCK, GPIO.OUT)
    GPIO.setup(NONMEDIS_DT, GPIO.IN)

    warmup(MEDIS_DT,  MEDIS_SCK,  "MEDIS   ")
    warmup(NONMEDIS_DT, NONMEDIS_SCK, "NON-MEDIS")

    nol_medis, noise_medis = auto_tare(MEDIS_DT, MEDIS_SCK, "MEDIS   ")
    nol_nonmedis, noise_nonmedis = auto_tare(NONMEDIS_DT, NONMEDIS_SCK, "NON-MEDIS")

    print("\n>> Kalibrasi Dasar Timbangan Selesai. Membuka Thread Utama...\n")
    time.sleep(0.5)

    global servo_pwm
    servo_pwm = GPIO.PWM(SERVO_PIN, 50)
    servo_pwm.start(posisi_sekarang)
    time.sleep(0.1)
    servo_pwm.ChangeDutyCycle(0)

    threads = [
        threading.Thread(target=thread_kamera, name="Kamera_Thread"),
        threading.Thread(target=thread_ultrasonic, daemon=True, name="Ultrasonic_Thread"),
        threading.Thread(target=thread_timbangan, daemon=True, name="Timbangan_Thread"),
        threading.Thread(target=thread_device_control_poller, daemon=True, name="DeviceControl_Thread")
    ]

    for t in threads:
        t.start()
        time.sleep(0.2)

    flask_thread = threading.Thread(target=jalankan_flask, daemon=True, name="Flask_Thread")
    flask_thread.start()

    print("\n=========================================================")
    print(" SERVER INTEGRASI AKTIF & GENERATING REAL-TIME DATA.")
    print("=========================================================\n")

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        shutdown_event.set()
        try:
            if servo_pwm is not None: servo_pwm.stop()
        except Exception: pass
        GPIO.cleanup()

if __name__ == "__main__":
    main()
