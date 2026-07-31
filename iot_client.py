"""
TRASH BIN - IOT CLIENT INTEGRATED WITH WEBSITE API
PATCHED V2 - All bugs fixed (A-M), config-driven, production-ready
======================================================================
"""

import cv2
import numpy as np
import tflite_runtime.interpreter as tflite
import os
import sys
import time
import json
import shutil
import logging
import logging.handlers
import subprocess
import threading
import statistics
import ipaddress
from functools import wraps
from typing import List, Tuple, Optional, Callable, Any

import RPi.GPIO as GPIO
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

# =====================================================================
# CONFIG LOADER
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "iot_config.json")

def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
        print(f"[CONFIG] Loaded dari {path}")
        return cfg
    except FileNotFoundError:
        print(f"[CONFIG-FATAL] File {path} tidak ditemukan!")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[CONFIG-FATAL] JSON invalid: {e}")
        sys.exit(1)

CFG = load_config(CONFIG_PATH)

# Shortcut aliases
PINS = CFG["gpio_pins"]
SERVO_CFG = CFG["servo"]
LC_CFG = CFG["loadcell"]
US_CFG = CFG["ultrasonic"]
CAM_CFG = CFG["camera"]
API_CFG = CFG["api"]
LOG_CFG = CFG["logging"]
FLASK_CFG = CFG["flask_local"]
DISK_CFG = CFG["disk_check"]
SYS_CFG = CFG["system"]

URL_WEBSITE_BASE = API_CFG["url_website_base"]
API_TRASH = URL_WEBSITE_BASE + "/api/trash"
API_WEIGHT = URL_WEBSITE_BASE + "/api/weight"
API_CAPACITY = URL_WEBSITE_BASE + "/api/capacity"
API_DEVICE_STATUS = URL_WEBSITE_BASE + "/api/device/status"
API_DEVICE_CONTROL = URL_WEBSITE_BASE + "/api/device"

# =====================================================================
# LOGGING SETUP (file + console + rotation)
# =====================================================================
os.makedirs(os.path.dirname(LOG_CFG["file"]), exist_ok=True) if os.path.dirname(LOG_CFG["file"]) else None

logger = logging.getLogger("iot_client")
logger.setLevel(getattr(logging, LOG_CFG["level"]))
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# File handler dengan rotation
file_handler = logging.handlers.RotatingFileHandler(
    LOG_CFG["file"],
    maxBytes=LOG_CFG["max_bytes"],
    backupCount=LOG_CFG["backup_count"]
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

if LOG_CFG["console"]:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def log_print(msg: str, level: str = "info"):
    """Compat layer: print() lama + logger baru."""
    getattr(logger, level)(msg)

# =====================================================================
# UTILITY: RETRY DECORATOR (bug B)
# =====================================================================
def retry(max_attempts: int = 3, backoff: float = 1.5, exceptions: tuple = (Exception,)):
    """Decorator untuk auto-retry dengan exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait = backoff * (2 ** (attempt - 1))
                        logger.warning(f"[RETRY] {func.__name__} attempt {attempt}/{max_attempts} gagal: {e}. Coba lagi dalam {wait:.1f}s")
                        time.sleep(wait)
            logger.error(f"[RETRY-FAIL] {func.__name__} gagal setelah {max_attempts} attempts: {last_exception}")
            raise last_exception
        return wrapper
    return decorator

# =====================================================================
# UTILITY: RATE LIMITER (bug G)
# =====================================================================
class RateLimiter:
    """Thread-safe rate limiter."""
    def __init__(self, max_per_second: float):
        self.min_interval = 1.0 / max_per_second if max_per_second > 0 else 0
        self.lock = threading.Lock()
        self.last_call = 0.0

    def acquire(self) -> bool:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed >= self.min_interval:
                self.last_call = now
                return True
            return False

    def wait_and_acquire(self):
        while not self.acquire():
            time.sleep(self.min_interval - (time.time() - self.last_call))

rate_weight = RateLimiter(API_CFG["rate_limit_weight_per_detik"])
rate_capacity = RateLimiter(API_CFG["rate_limit_capacity_per_detik"])
rate_trash = RateLimiter(API_CFG["rate_limit_trash_per_detik"])

# =====================================================================
# UTILITY: DISK SPACE CHECK (bug F)
# =====================================================================
def check_disk_space(path: str = "/", min_free_mb: int = DISK_CFG["min_free_mb"]) -> bool:
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free / (1024 * 1024)
        return free_mb >= min_free_mb
    except Exception as e:
        logger.error(f"[DISK-CHECK] Gagal: {e}")
        return False

os.makedirs(DISK_CFG["upload_path"], exist_ok=True)

# =====================================================================
# UTILITY: IP WHITELIST (bug I - Flask auth)
# =====================================================================
def ip_allowed(client_ip: str, allowed_cidrs: List[str]) -> bool:
    try:
        client = ipaddress.ip_address(client_ip)
        for cidr in allowed_cidrs:
            if client in ipaddress.ip_network(cidr, strict=False):
                return True
    except ValueError:
        pass
    return False

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

def log_error(msg: str):
    logger.error(msg)
    with state_lock:
        shared_state["errors"].append({"time": time.time(), "message": msg})
        shared_state["errors"] = shared_state["errors"][-20:]

# =====================================================================
# API CALLS (dengan retry + rate limit)
# =====================================================================
_last_error_report = {}
_error_report_lock = threading.Lock()

def _report_error_status(device_name: str):
    """Debounced ERROR report - max 1 per device per cooldown."""
    now = time.time()
    with _error_report_lock:
        last = _last_error_report.get(device_name, 0)
        if now - last < SYS_CFG["error_report_cooldown_detik"]:
            return
        _last_error_report[device_name] = now
    kirim_status_device(device_name, "ERROR")

@retry(max_attempts=API_CFG["max_retry"], backoff=API_CFG["retry_backoff_detik"])
def _post_request(url: str, json_payload: Optional[dict] = None, data_payload: Optional[dict] = None, files: Optional[dict] = None) -> requests.Response:
    if files:
        return requests.post(url, data=data_payload, files=files, timeout=API_CFG["timeout_detik"])
    return requests.post(url, json=json_payload, timeout=API_CFG["timeout_detik"])

def kirim_api_async(url: str, payload: dict, files: dict = None,
                    device_name_on_error: Optional[str] = None,
                    rate_limiter: Optional[RateLimiter] = None):
    """Kirim POST non-blocking. Auto-retry, rate-limit aware, error reporting."""
    def run():
        if rate_limiter:
            rate_limiter.wait_and_acquire()

        # Bug F: disk space check sebelum upload gambar
        if files and not check_disk_space():
            logger.error(f"[DISK] Space rendah, skip upload ke {url}")
            if device_name_on_error:
                _report_error_status(device_name_on_error)
            return

        try:
            response = _post_request(url, json_payload=payload if not files else None,
                                    data_payload=payload if files else None,
                                    files=files)
            if response.status_code not in [200, 201]:
                logger.warning(f"[API-WARN] {url} HTTP {response.status_code}")
                if device_name_on_error:
                    _report_error_status(device_name_on_error)
            else:
                logger.debug(f"[API-OK] {url} {response.status_code}")
        except Exception as e:
            logger.error(f"[API-ERROR] {url} -> {e}")
            if device_name_on_error:
                _report_error_status(device_name_on_error)

    threading.Thread(target=run, daemon=True).start()

@retry(max_attempts=2, backoff=1.0)
def kirim_status_device(device_name: str, status_str: str):
    """Kirim status device langsung (synchronous, dengan retry)."""
    payload = {"device": device_name, "status": status_str}
    response = requests.post(API_DEVICE_STATUS, json=payload, timeout=2.0)
    if response.status_code in [200, 201]:
        logger.info(f"[STATUS] {device_name} -> {status_str}")
    else:
        logger.warning(f"[STATUS-FAIL] {device_name}: HTTP {response.status_code}")
        raise Exception(f"HTTP {response.status_code}")

def kirim_status_semua_device():
    """Sinkronkan status SEMUA device ke server."""
    statuses = {
        "Sensor Raspberry Pi": "ON" if not shutdown_event.is_set() else "OFF",
        "Sensor Kamera": "ON" if camera_enabled.is_set() else "OFF",
        "Sensor Ultrasonik Medis": "ON" if ultrasonik_medis_enabled.is_set() else "ERROR",
        "Sensor Ultrasonik Non-Medis": "ON" if ultrasonik_non_medis_enabled.is_set() else "ERROR",
        "Sensor Load Cell Medis": "ON" if loadcell_medis_enabled.is_set() else "ERROR",
        "Sensor Load Cell Non-Medis": "ON" if loadcell_nonmedis_enabled.is_set() else "ERROR",
    }
    for name, status in statuses.items():
        try:
            kirim_status_device(name, status)
        except Exception as e:
            logger.error(f"[STATUS-SYNC] {name} gagal: {e}")
        time.sleep(0.05)

# =====================================================================
# EVENTS ENABLE/DISABLE SETIAP SENSOR
# =====================================================================
recalibrate_counter = [0]  # bug C: pakai counter bukan flag (agar request tidak hilang)
recalibrate_lock = threading.Lock()

def request_recalibrate():
    with recalibrate_lock:
        recalibrate_counter[0] += 1

def consume_recalibrate() -> bool:
    with recalibrate_lock:
        if recalibrate_counter[0] > 0:
            recalibrate_counter[0] -= 1
            return True
        return False

shutdown_event = threading.Event()

camera_enabled = threading.Event()
camera_enabled.set()

ultrasonik_medis_enabled = threading.Event()
ultrasonik_medis_enabled.set()

ultrasonik_non_medis_enabled = threading.Event()
ultrasonik_non_medis_enabled.set()

loadcell_medis_enabled = threading.Event()
loadcell_medis_enabled.set()

loadcell_nonmedis_enabled = threading.Event()
loadcell_nonmedis_enabled.set()


# =====================================================================
# 1. KONFIGURASI PIN HARDWARE & TUNING SENSOR (dari config.json)
# =====================================================================
SERVO_PIN = PINS["servo"]

TRIG_MEDIS = PINS["trig_medis"]
ECHO_MEDIS = PINS["echo_medis"]
TRIG_NON_MEDIS = PINS["trig_non_medis"]
ECHO_NON_MEDIS = PINS["echo_non_medis"]

MEDIS_DT = PINS["medis_dt"]
MEDIS_SCK = PINS["medis_sck"]
NONMEDIS_DT = PINS["nonmedis_dt"]
NONMEDIS_SCK = PINS["nonmedis_sck"]

MEDIS_FAKTOR = LC_CFG["faktor_medis"]
NONMEDIS_FAKTOR = LC_CFG["faktor_nonmedis"]

SAMPEL_WARMUP = LC_CFG["sampel_warmup"]
SAMPEL_TARE = LC_CFG["sampel_tare"]
SAMPEL_BACA = LC_CFG["sampel_baca"]
WINDOW_TAMPIL = LC_CFG["window_tampil"]
SIGMA_FILTER = LC_CFG["sigma_filter"]

servo_pwm = None
posisi_sekarang = SERVO_CFG["duty_tengah"]
servo_lock = threading.Lock()

def gerakkan_servo_halus(sudut_tujuan):
    """Gerakkan servo dengan step halus. Konfigurasi dari SERVO_CFG."""
    global posisi_sekarang, servo_pwm
    if servo_pwm is None:
        return

    with servo_lock:
        if sudut_tujuan == "kiri":
            duty_target = SERVO_CFG["duty_kiri"]
        elif sudut_tujuan == "kanan":
            duty_target = SERVO_CFG["duty_kanan"]
        else:
            duty_target = SERVO_CFG["duty_tengah"]

        if posisi_sekarang == duty_target:
            return

        step = SERVO_CFG["step"]
        delay_ms = SERVO_CFG["delay_step_ms"] / 1000.0
        sementara = posisi_sekarang

        while abs(duty_target - sementara) > step / 2:
            sementara += step if duty_target > sementara else -step
            servo_pwm.ChangeDutyCycle(sementara)
            time.sleep(delay_ms)

        servo_pwm.ChangeDutyCycle(duty_target)
        posisi_sekarang = duty_target
        time.sleep(0.2)
        servo_pwm.ChangeDutyCycle(0)

# =====================================================================
# 2. ALGORITMA CORE TIMBANGAN
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
    logger.info(f"   Warmup {label}")
    for _ in range(SAMPEL_WARMUP):
        try:
            baca_raw(pin_dt, pin_sck)
        except TimeoutError:
            pass
        time.sleep(0.05)
    logger.info(f"   Warmup {label} OK")

def auto_tare(pin_dt: int, pin_sck: int, label: str) -> Tuple[float, float]:
    logger.info(f"   Tare {label}")
    data = ambil_sampel(pin_dt, pin_sck, SAMPEL_TARE, jeda_ms=10)
    nilai, noise = rata_bersih(data)
    logger.info(f"   Tare {label} OK (raw={nilai:.0f}, noise=±{noise:.0f})")
    return nilai, noise

# =====================================================================
# 3. THREAD AI KAMERA
# =====================================================================
HEADLESS_MODE = not (os.environ.get("DISPLAY") or sys.platform == "win32")

def thread_kamera():
    MODEL_PATH = os.path.join(BASE_DIR, "model_klasifikasi.tflite")

    logger.info("--- [KAMERA] Memuat Model TensorFlow Lite ---")
    try:
        interpreter = tflite.Interpreter(model_path=MODEL_PATH)
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
        # bug E: Validasi output shape untuk pastikan model sesuai
        output_shape = output_details[0]["shape"]
        logger.info(f"[KAMERA] Model output shape: {output_shape}")
    except Exception as e:
        log_error(f"[KAMERA] Gagal memuat model: {e}")
        _report_error_status("Sensor Kamera")
        return

    RAW_FRAME_PATH = "/dev/shm/live_frame.jpg"
    cmd = [
        "libcamera-still", "-t", "0", "--timelapse", "40",
        "--width", str(CAM_CFG["frame_width"]), "--height", str(CAM_CFG["frame_height"]),
        "-o", RAW_FRAME_PATH, "-n", "--immediate",
    ]

    camera_process = None
    def start_camera():
        nonlocal camera_process
        try:
            subprocess.run(["sudo", "killall", "-9", "libcamera-vid", "libcamera-still"],
                         stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            if os.path.exists(RAW_FRAME_PATH):
                subprocess.run(["sudo", "rm", "-f", RAW_FRAME_PATH])
            camera_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("[KAMERA] Sensor kamera aktif")
            time.sleep(2.0)
            return True
        except Exception as e:
            log_error(f"[KAMERA] Gagal start: {e}")
            return False

    if not start_camera():
        _report_error_status("Sensor Kamera")
        return

    THRESHOLD_AKURASI = CAM_CFG["threshold_akurasi"]
    MOTION_THRESHOLD = CAM_CFG["motion_threshold"]
    MOTION_MIN_AREA = CAM_CFG["motion_min_area"]
    KALIBRASI_FRAME = CAM_CFG["kalibrasi_frame"]
    MODEL_LABELS = CAM_CFG["model_labels"]

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
        # bug E: gunakan labels dari config, bukan asumsi
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (224, 224))
        # Normalisasi ke rentang [-1, 1] (Standar Teachable Machine / MobileNet)
        data = (np.expand_dims(resized, axis=0).astype(np.float32) / 127.5) - 1.0
        interpreter.set_tensor(input_details[0]["index"], data)
        interpreter.invoke()
        pred = interpreter.get_tensor(output_details[0]["index"])[0]

        if len(pred) > 1:
            probs = {label: float(pred[i]) * 100 for i, label in enumerate(MODEL_LABELS)}
        else:
            # Binary fallback
            prob_NM = float(pred[0]) * 100
            prob_M = (1.0 - float(pred[0])) * 100
            probs = {"Medical": prob_M, "Non Medical": prob_NM}
            
        best_label = max(probs, key=probs.get)
        return best_label, probs[best_label], probs.get("Medical", 0.0), probs.get("Non Medical", 0.0)

    logger.info("--- [KAMERA] Kalibrasi Background ---")
    background = None
    jumlah_frame_kalibrasi = 0
    while jumlah_frame_kalibrasi < KALIBRASI_FRAME:
        if shutdown_event.is_set():
            return
        frame = baca_frame_aman()
        if frame is None:
            time.sleep(0.05)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if background is None:
            background = gray.astype(np.float32)
        else:
            cv2.accumulateWeighted(gray, background, CAM_CFG["alpha_kalibrasi"])
        jumlah_frame_kalibrasi += 1
    background_ref = cv2.convertScaleAbs(background)
    logger.info("[KAMERA] Kalibrasi selesai! Sensor siap.")

    STATE = "STANDBY"
    objek_terkunci = None
    waktu_mulai_kunci = None
    last_frame_time = 0
    keputusan_final = ""
    confidence_final = 0.0
    terakhir_kirim_trash = 0.0  # bug L: tracker untuk hindari duplikat

    try:
        while not shutdown_event.is_set():
            if not camera_enabled.is_set():
                time.sleep(0.5)
                continue

            # bug C: consume recalibrate request (bukan flag yang bisa hilang)
            if consume_recalibrate():
                frame_recal = baca_frame_aman()
                if frame_recal is not None:
                    gray_recal = cv2.cvtColor(frame_recal, cv2.COLOR_BGR2GRAY)
                    gray_recal = cv2.GaussianBlur(gray_recal, (21, 21), 0)
                    background = gray_recal.astype(np.float32)
                    background_ref = cv2.convertScaleAbs(background)
                    STATE = "STANDBY"
                    objek_terkunci = None
                    waktu_mulai_kunci = None
                    keputusan_final = ""
                    logger.info("[KAMERA] Recalibrated")

            if camera_process.poll() is not None:
                logger.warning("[KAMERA] Process mati, restart...")
                if not start_camera():
                    _report_error_status("Sensor Kamera")
                    time.sleep(5)
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
                cv2.accumulateWeighted(gray_now, background, CAM_CFG["alpha_standby"])
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
                    class_idx = MODEL_LABELS.index(prediksi_sekarang) if prediksi_sekarang in MODEL_LABELS else -1

                    # FIX BUG "FALLBACK KENA PAUSE":
                    # Sebelumnya: setiap kali akurasi turun <= threshold, waktu_mulai_kunci di-reset
                    # ke None. Ini bikin lock tidak pernah selesai kalau confidence oscillates
                    # di sekitar threshold (kasus umum untuk objek kecil seperti sampah 45g).
                    #
                    # Fix: Jangan reset lock kalau label yang diprediksi SAMA dengan label
                    # yang sedang di-lock. Confidence boleh fluktuatif, yang penting label konsisten.
                    # Hanya reset kalau label BERUBAH atau confidence drop drastis (<30%).
                    if akurasi_tampil <= THRESHOLD_AKURASI:
                        if prediksi_sekarang != objek_terkunci or akurasi_tampil < 30.0:
                            # Label berubah atau confidence terlalu rendah - reset lock
                            status_countdown = f"Menganalisa... ({akurasi_tampil:.1f}%)"
                            objek_terkunci = None
                            waktu_mulai_kunci = None
                        # else: label sama, confidence turun sedikit - JANGAN reset, biarkan lock berjalan
                    else:
                        if objek_terkunci != prediksi_sekarang:
                            objek_terkunci = prediksi_sekarang
                            waktu_mulai_kunci = time.time()

                    # Hitung durasi lock (default 0 jika None)
                    if waktu_mulai_kunci is not None:
                        durasi = time.time() - waktu_mulai_kunci
                    else:
                        durasi = 0.0

                    if objek_terkunci is not None:
                        if durasi < 1.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 1/3"
                        elif durasi < 2.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 2/3"
                        elif durasi < 3.0:
                            status_countdown = f"Mengunci {objek_terkunci}... 3/3"
                        else:
                            # FALLBACK TIMEOUT: jika sudah >3s lock, langsung lanjut meskipun confidence < threshold
                            keputusan_final = objek_terkunci
                            confidence_final = akurasi_tampil
                            STATE = "MEMBUANG"
                    else:
                        status_countdown = f"Menganalisa... ({akurasi_tampil:.1f}%)"

                label_teks = f"{prediksi_sekarang} ({akurasi_tampil:.1f}%)" if akurasi_tampil > 0 else ""
                warna_teks = (0, 0, 255) if class_idx == 0 else (0, 255, 0)

            elif STATE == "MEMBUANG":
                status_countdown = f"MEMBUANG ({keputusan_final.upper()}) - Lock Model..."
                cv2.putText(frame, "LISTRIK DROP DIABAIKAN", (15, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

                # bug L: Cek apakah sudah kirim dalam window waktu (hindari duplikat)
                now = time.time()
                if now - terakhir_kirim_trash >= 5.0:
                    terakhir_kirim_trash = now

                    nama_foto = f"trash_{int(now)}.jpg"
                    _, img_encoded = cv2.imencode('.jpg', frame)

                    payload_trash = {
                        "kategori": keputusan_final,
                        "jenis_sampah": keputusan_final,
                        "confidence": round(float(confidence_final), 2)
                    }
                    files_trash = {'image': (nama_foto, img_encoded.tobytes(), 'image/jpeg')}

                    logger.info(f"[API] Mengirim Data Sampah: {keputusan_final} ({confidence_final}%)")
                    kirim_api_async(API_TRASH, payload_trash, files=files_trash,
                                  device_name_on_error="Sensor Kamera",
                                  rate_limiter=rate_trash)
                else:
                    logger.debug(f"[API] Skip duplicate trash send (last sent {now - terakhir_kirim_trash:.1f}s ago)")

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

            # bug H: Skip imshow kalau headless (no DISPLAY env)
            if not HEADLESS_MODE:
                try:
                    cv2.imshow('Deteksi Sampah Real-time', frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('r'):
                        request_recalibrate()
                except cv2.error:
                    HEADLESS_MODE = True
                    logger.warning("[KAMERA] Display error, switch ke headless mode")

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
        _report_error_status("Sensor Kamera")
    finally:
        cv2.destroyAllWindows()
        try:
            if camera_process:
                camera_process.terminate()
        except Exception:
            pass

# =====================================================================
# 4. THREAD DUAL ULTRASONIC
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

        timeout_limit = time.time() + US_CFG["timeout_detik"]
        while GPIO.input(pin_echo) == 0:
            if time.time() > timeout_limit:
                return None
        pancaran_mulai = time.time()

        timeout_limit = time.time() + US_CFG["timeout_detik"]
        while GPIO.input(pin_echo) == 1:
            if time.time() > timeout_limit:
                return None
        pancaran_selesai = time.time()

        jarak_cm = round(((pancaran_selesai - pancaran_mulai) * 34300) / 2, 1)

        if jarak_cm < US_CFG["min_valid_cm"] or jarak_cm > US_CFG["max_valid_cm"]:
            return None

        return jarak_cm

    def cek_kapasitas(jarak):
        if jarak is None:
            return "Error"
        if jarak <= US_CFG["threshold_penuh_cm"]:
            return "Penuh"
        if jarak <= US_CFG["threshold_hampir_penuh_cm"]:
            return "Hampir Penuh"
        return "Kosong"

    error_count_medis = 0
    error_count_nonmedis = 0

    while not shutdown_event.is_set():
        try:
            j_medis = None
            j_non_medis = None
            stat_medis = "OFF"
            stat_non_medis = "OFF"

            if ultrasonik_medis_enabled.is_set():
                j_medis = ambil_jarak(TRIG_MEDIS, ECHO_MEDIS)
                if j_medis is None:
                    stat_medis = "Error"
                    error_count_medis += 1
                    if error_count_medis >= SYS_CFG["error_threshold_for_status"]:
                        _report_error_status("Sensor Ultrasonik Medis")
                        error_count_medis = 0
                else:
                    stat_medis = cek_kapasitas(j_medis)
                    error_count_medis = 0

            if ultrasonik_non_medis_enabled.is_set():
                j_non_medis = ambil_jarak(TRIG_NON_MEDIS, ECHO_NON_MEDIS)
                if j_non_medis is None:
                    stat_non_medis = "Error"
                    error_count_nonmedis += 1
                    if error_count_nonmedis >= SYS_CFG["error_threshold_for_status"]:
                        _report_error_status("Sensor Ultrasonik Non-Medis")
                        error_count_nonmedis = 0
                else:
                    stat_non_medis = cek_kapasitas(j_non_medis)
                    error_count_nonmedis = 0

            update_state("capacity", {
                "medis": {"jarak_cm": j_medis, "status": stat_medis},
                "non_medis": {"jarak_cm": j_non_medis, "status": stat_non_medis},
            })

            logger.info(f"[ULTRASONIC] Medis: {j_medis if j_medis else 'OFF'}cm ({stat_medis}) | Non-Medis: {j_non_medis if j_non_medis else 'OFF'}cm ({stat_non_medis})")

            payload_capacity = {
                "medis": {"jarak": j_medis if j_medis else 0, "status": stat_medis},
                "non_medis": {"jarak": j_non_medis if j_non_medis else 0, "status": stat_non_medis}
            }
            kirim_api_async(API_CAPACITY, payload_capacity,
                          device_name_on_error="Sensor Ultrasonik Medis",
                          rate_limiter=rate_capacity)

        except Exception as e:
            log_error(f"[ULTRASONIC] Loop error: {e}")
        time.sleep(US_CFG["interval_kirim_detik"])

def format_berat(gram: float) -> str:
    if gram >= 1000:
        return f"{gram / 1000:.2f} kg"
    return f"{gram:.1f} g"

# =====================================================================
# 5. THREAD DUAL TIMBANGAN
# =====================================================================
def thread_timbangan():
    global nol_medis, noise_medis, nol_nonmedis, noise_nonmedis

    # bug K: Validasi noise HX711 - kalau noise terlalu tinggi, sensor unreliable
    reliable_medis = noise_medis <= LC_CFG["max_noise_for_reliable"]
    reliable_nonmedis = noise_nonmedis <= LC_CFG["max_noise_for_reliable"]
    if not reliable_medis:
        logger.warning(f"[TIMBANGAN] Noise MEDIS terlalu tinggi ({noise_medis:.0f}), sensor unreliable")
        _report_error_status("Sensor Load Cell Medis")
    if not reliable_nonmedis:
        logger.warning(f"[TIMBANGAN] Noise NON-MEDIS terlalu tinggi ({noise_nonmedis:.0f}), sensor unreliable")
        _report_error_status("Sensor Load Cell Non-Medis")

    dz_medis = max(LC_CFG["dead_zone_min_gram"], (noise_medis / MEDIS_FAKTOR) * 2.0)
    dz_nonmedis = max(LC_CFG["dead_zone_min_gram"], (noise_nonmedis / NONMEDIS_FAKTOR) * 2.0)

    hist_medis: List[float] = []
    hist_nonmedis: List[float] = []

    berat_m_prev = 0.0
    berat_n_prev = 0.0

    error_count_medis = 0
    error_count_nonmedis = 0

    while not shutdown_event.is_set():
        try:
            tampil_m = 0.0
            tampil_n = 0.0

            if loadcell_medis_enabled.is_set() and reliable_medis:
                data_m = ambil_sampel(MEDIS_DT, MEDIS_SCK, SAMPEL_BACA, jeda_ms=5)
                if not data_m:
                    error_count_medis += 1
                    if error_count_medis >= SYS_CFG["error_threshold_for_status"]:
                        _report_error_status("Sensor Load Cell Medis")
                        error_count_medis = 0
                else:
                    error_count_medis = 0
                    raw_m, _ = rata_bersih(data_m)
                    berat_m = (raw_m - nol_medis) / MEDIS_FAKTOR
                    if abs(berat_m) < dz_medis:
                        berat_m = 0.0

                    if abs(berat_m - berat_m_prev) > 20:
                        hist_medis.clear()
                    berat_m_prev = berat_m

                    hist_medis.append(berat_m)
                    if len(hist_medis) > WINDOW_TAMPIL:
                        hist_medis.pop(0)

                    tampil_m = max(0.0, round(statistics.mean(hist_medis), 1))

            if loadcell_nonmedis_enabled.is_set() and reliable_nonmedis:
                data_n = ambil_sampel(NONMEDIS_DT, NONMEDIS_SCK, SAMPEL_BACA, jeda_ms=5)
                if not data_n:
                    error_count_nonmedis += 1
                    if error_count_nonmedis >= SYS_CFG["error_threshold_for_status"]:
                        _report_error_status("Sensor Load Cell Non-Medis")
                        error_count_nonmedis = 0
                else:
                    error_count_nonmedis = 0
                    raw_n, _ = rata_bersih(data_n)
                    berat_n = (raw_n - nol_nonmedis) / NONMEDIS_FAKTOR
                    if abs(berat_n) < dz_nonmedis:
                        berat_n = 0.0

                    if abs(berat_n - berat_n_prev) > 20:
                        hist_nonmedis.clear()
                    berat_n_prev = berat_n

                    hist_nonmedis.append(berat_n)
                    if len(hist_nonmedis) > WINDOW_TAMPIL:
                        hist_nonmedis.pop(0)

                    tampil_n = max(0.0, round(statistics.mean(hist_nonmedis), 1))

            update_state("weight", {
                "medis": {"gram": tampil_m},
                "non_medis": {"gram": tampil_n},
            })

            logger.info(f"[TIMBANGAN] Medis: {format_berat(tampil_m) if loadcell_medis_enabled.is_set() else 'OFF'} | Non-Medis: {format_berat(tampil_n) if loadcell_nonmedis_enabled.is_set() else 'OFF'}")

            payload_weight = {
                "medis": tampil_m,
                "non_medis": tampil_n
            }
            kirim_api_async(API_WEIGHT, payload_weight,
                          device_name_on_error="Sensor Load Cell Medis",
                          rate_limiter=rate_weight)

        except Exception as e:
            log_error(f"[TIMBANGAN] Loop error: {e}")
        time.sleep(1.5)

# =====================================================================
# THREAD DEVICE STATUS SYNC
# =====================================================================
def thread_device_status_sync():
    logger.info("[SYSTEM] Thread Device Status Sync Aktif.")
    while not shutdown_event.is_set():
        if shutdown_event.wait(timeout=SYS_CFG["sync_interval_detik"]):
            break
        try:
            kirim_status_semua_device()
            logger.info(f"[DEVICE-SYNC] Status device tersinkron ({time.strftime('%H:%M:%S')})")
        except Exception as e:
            log_error(f"[DEVICE-SYNC] Gagal sync: {e}")

# =====================================================================
# THREAD DEVICE CONTROL POLLER
# =====================================================================
def thread_device_control_poller():
    logger.info("[SYSTEM] Thread Poller Kontrol Device Aktif.")
    while not shutdown_event.is_set():
        try:
            response = requests.get(API_DEVICE_CONTROL, timeout=2.0)
            if response.status_code == 200:
                data = response.json()

                devices = data.get("devices", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

                for dev in devices:
                    nama_perangkat = dev.get("device")
                    status_on = (dev.get("status", "ON").upper() == "ON")

                    if nama_perangkat == "Sensor Kamera":
                        if status_on and not camera_enabled.is_set():
                            camera_enabled.set()
                            logger.info("[POLLER] Sensor Kamera ON")
                            kirim_status_device("Sensor Kamera", "ON")
                        elif not status_on and camera_enabled.is_set():
                            camera_enabled.clear()
                            logger.info("[POLLER] Sensor Kamera OFF")
                            kirim_status_device("Sensor Kamera", "OFF")

                    elif nama_perangkat == "Sensor Ultrasonik Medis":
                        if status_on and not ultrasonik_medis_enabled.is_set():
                            ultrasonik_medis_enabled.set()
                            logger.info("[POLLER] Sensor Ultrasonik Medis ON")
                            kirim_status_device("Sensor Ultrasonik Medis", "ON")
                        elif not status_on and ultrasonik_medis_enabled.is_set():
                            ultrasonik_medis_enabled.clear()
                            logger.info("[POLLER] Sensor Ultrasonik Medis OFF")
                            kirim_status_device("Sensor Ultrasonik Medis", "OFF")

                    elif nama_perangkat == "Sensor Ultrasonik Non-Medis":
                        if status_on and not ultrasonik_non_medis_enabled.is_set():
                            ultrasonik_non_medis_enabled.set()
                            logger.info("[POLLER] Sensor Ultrasonik Non-Medis ON")
                            kirim_status_device("Sensor Ultrasonik Non-Medis", "ON")
                        elif not status_on and ultrasonik_non_medis_enabled.is_set():
                            ultrasonik_non_medis_enabled.clear()
                            logger.info("[POLLER] Sensor Ultrasonik Non-Medis OFF")
                            kirim_status_device("Sensor Ultrasonik Non-Medis", "OFF")

                    elif nama_perangkat == "Sensor Load Cell Medis":
                        if status_on and not loadcell_medis_enabled.is_set():
                            loadcell_medis_enabled.set()
                            logger.info("[POLLER] Sensor Load Cell Medis ON")
                            kirim_status_device("Sensor Load Cell Medis", "ON")
                        elif not status_on and loadcell_medis_enabled.is_set():
                            loadcell_medis_enabled.clear()
                            logger.info("[POLLER] Sensor Load Cell Medis OFF")
                            kirim_status_device("Sensor Load Cell Medis", "OFF")

                    elif nama_perangkat == "Sensor Load Cell Non-Medis":
                        if status_on and not loadcell_nonmedis_enabled.is_set():
                            loadcell_nonmedis_enabled.set()
                            logger.info("[POLLER] Sensor Load Cell Non-Medis ON")
                            kirim_status_device("Sensor Load Cell Non-Medis", "ON")
                        elif not status_on and loadcell_nonmedis_enabled.is_set():
                            loadcell_nonmedis_enabled.clear()
                            logger.info("[POLLER] Sensor Load Cell Non-Medis OFF")
                            kirim_status_device("Sensor Load Cell Non-Medis", "OFF")

                    elif nama_perangkat == "Sensor Raspberry Pi":
                        if not status_on:
                            grace = SYS_CFG["shutdown_grace_detik"]
                            logger.warning(f"[POLLER] ⚠️  Website memerintahkan SHUTDOWN dalam {grace:.0f} detik!")
                            logger.warning("[POLLER] Tekan Ctrl+C untuk membatalkan.")
                            for i in range(int(grace), 0, -1):
                                if shutdown_event.is_set():
                                    break
                                logger.warning(f"[POLLER] Shutdown dalam {i}s...")
                                time.sleep(1.0)
                            if not shutdown_event.is_set():
                                logger.warning("[POLLER] Shutdown dieksekusi.")
                                shutdown_event.set()

        except Exception as e:
            logger.error(f"[POLLER-ERROR] Gagal membaca status kontrol: {e}")
        time.sleep(2.0)

# =====================================================================
# 6. FLASK API SERVER LOCAL (dengan IP whitelist + token)
# =====================================================================
app = Flask(__name__)
CORS(app)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        client_ip = request.remote_addr or "0.0.0.0"

        # IP whitelist check
        if not ip_allowed(client_ip, FLASK_CFG["allowed_ips"]):
            logger.warning(f"[FLASK-AUTH] IP ditolak: {client_ip}")
            return jsonify({"error": "Forbidden - IP not in whitelist"}), 403

        # Token check
        token = request.headers.get("X-Auth-Token") or request.args.get("token")
        if token != FLASK_CFG["auth_token"]:
            logger.warning(f"[FLASK-AUTH] Token invalid dari {client_ip}")
            return jsonify({"error": "Forbidden - invalid token"}), 403

        return f(*args, **kwargs)
    return decorated

@app.route("/", methods=["GET"])
@require_auth
def home():
    with state_lock:
        return jsonify(shared_state)

@app.route("/api/status", methods=["GET"])
@require_auth
def status():
    with state_lock:
        return jsonify(shared_state)

@app.route("/api/recalibrate", methods=["POST", "GET"])
@require_auth
def api_recalibrate():
    request_recalibrate()
    return jsonify({"status": "ok", "message": "Kalibrasi ulang diminta."})

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "uptime": time.time()})

def jalankan_flask():
    logger.info(f"[FLASK] Local server di {FLASK_CFG['host']}:{FLASK_CFG['port']}")
    app.run(host=FLASK_CFG["host"], port=FLASK_CFG["port"], threaded=True, use_reloader=False)

# =====================================================================
# 7. EXECUTOR UTAMA
# =====================================================================
def main():
    global nol_medis, noise_medis, nol_nonmedis, noise_nonmedis

    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║      SISTEM INTEGRASI TRASH BIN - CALIBRATING        ║")
    logger.info("╚══════════════════════════════════════════════════════╝")
    logger.info("[SYSTEM] Pastikan KEDUA wadah timbangan KOSONG!")

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

    GPIO.output(TRIG_MEDIS, GPIO.LOW)
    GPIO.output(TRIG_NON_MEDIS, GPIO.LOW)
    time.sleep(0.5)

    warmup(MEDIS_DT, MEDIS_SCK, "MEDIS   ")
    warmup(NONMEDIS_DT, NONMEDIS_SCK, "NON-MEDIS")

    nol_medis, noise_medis = auto_tare(MEDIS_DT, MEDIS_SCK, "MEDIS   ")
    nol_nonmedis, noise_nonmedis = auto_tare(NONMEDIS_DT, NONMEDIS_SCK, "NON-MEDIS")

    logger.info(">> Kalibrasi Dasar Timbangan Selesai.")
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
        threading.Thread(target=thread_device_control_poller, daemon=True, name="DeviceControl_Thread"),
        threading.Thread(target=thread_device_status_sync, daemon=True, name="DeviceSync_Thread"),
    ]

    for t in threads:
        t.start()
        time.sleep(0.2)

    flask_thread = threading.Thread(target=jalankan_flask, daemon=True, name="Flask_Thread")
    flask_thread.start()

    time.sleep(2.0)
    logger.info("[SYSTEM] Initial sync device status...")
    try:
        kirim_status_semua_device()
        logger.info("[SYSTEM] Initial sync selesai.")
    except Exception as e:
        logger.error(f"[SYSTEM] Initial sync gagal: {e}")

    logger.info("=========================================================")
    logger.info(" SERVER INTEGRASI AKTIF & GENERATING REAL-TIME DATA.")
    logger.info("=========================================================")

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except (KeyboardInterrupt, SystemExit):
        logger.info("[SYSTEM] Keyboard interrupt received. Shutting down...")
    finally:
        shutdown_event.set()
        try:
            if servo_pwm is not None:
                servo_pwm.stop()
        except Exception:
            pass
        GPIO.cleanup()
        logger.info("[SYSTEM] Shutdown complete.")

if __name__ == "__main__":
    main()