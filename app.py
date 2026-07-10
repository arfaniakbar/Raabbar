try:
    import gevent.monkey
    gevent.monkey.patch_all()
except ImportError:
    pass

from flask import Flask, render_template, request, session
from models.bin_status import BinStatus

import os
import json
from flask_socketio import SocketIO
import uuid
from threading import Lock, RLock

# Global lock untuk mencegah tabrakan SQLite di gevent
db_lock = RLock()

from flask import jsonify
from werkzeug.utils import secure_filename
from datetime import datetime
from datetime import timedelta
import calendar

import pytz

import pytz
WITA = pytz.timezone("Asia/Makassar")

def format_wita(dt, fmt="%d %b %Y %H:%M"):
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt).astimezone(WITA)
    else:
        dt = dt.astimezone(WITA)
    return dt.strftime(fmt)


from config import Config

from database.db import db
from flask import redirect, make_response
from flask import url_for
from models.user import User
from auth import (
    generate_token, decode_token, get_token_from_request, get_current_user,
    login_required, set_auth_cookie, clear_auth_cookie
)


# ==================================================
# APP
# ==================================================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "raabbar-secret-key-change-me")

app.jinja_env.globals['format_wita'] = format_wita
app.jinja_env.globals['now_wita'] = lambda: datetime.now(WITA)

# Global lock (db_lock) sudah dipakai secara spesifik di tiap route (with db_lock:) 
# jadi kita TIDAK PERLU me-lock seluruh request di before_request. 
# Jika seluruh request di-lock, Socket.IO long-polling akan menahan lock selama 60 detik
# dan membuat semua request API dari Raspberry Pi menjadi Time Out!


app.config.from_object(Config)

UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "static",
    "uploads",
    "history"
)

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)

# ==================================================
# DATABASE
# ==================================================

db.init_app(app)

# ==================================================
# SOCKET
# ==================================================

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# ==========================================
# SEED ADMIN (SUDAH DIHAPUS AGAR TIDAK DEADLOCK)
# ==========================================

# ==========================================
# DATA: Daftar device IoT
# ==========================================

timezone = pytz.timezone('Asia/Jakarta')

devices = [
    {
        "id": 1,
        "name": "Sensor Raspberry Pi",
        "status": "ON"
    },
    {
        "id": 2,
        "name": "Sensor Kamera",
        "status": "ON"
    },
    {
        "id": 3,
        "name": "Sensor Ultrasonik Non-Medis",
        "status": "ON"
    },
    {
        "id": 4,
        "name": "Sensor Load Cell Medis",
        "status": "ON"
    },
    {
        "id": 5,
        "name": "Sensor Load Cell Non-Medis",
        "status": "ON"
    },
    {
        "id": 6,
        "name": "Sensor Ultrasonik Medis",
        "status": "ON"
    }
]

# ==================================================
# CREATE TABLE & SEEDING (SUDAH DIHAPUS AGAR TIDAK DEADLOCK)
# ==================================================
from models.sampah import Sampah
from models.berat_sampah import BeratSampah
from models.pengambilan import PengambilanSampah

from models.trash_log import TrashLog
from models.device_log import DeviceLog
from models.history_log import HistoryLog

# Database sudah dibuat, jadi kita tidak perlu mengeksekusinya lagi di setiap worker gunicorn.

# ==================================================
# DASHBOARD
# ==================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        user = User.query.first()
        if user and user.check_password(password):
            from datetime import datetime, timezone
            user.last_login = datetime.now(timezone.utc)
            with db_lock:
                db.session.commit()
            token = generate_token(user.id, user.username)
            session["username"] = user.username
            session["avatar_url"] = user.avatar_url
            next_url = request.args.get('next') or request.form.get('next') or '/'
            response = make_response(redirect(next_url))
            set_auth_cookie(response, token)
            return response
        error = 'Password salah'
    # GET request - if already logged in, redirect to /
    if get_current_user():
        return redirect('/')
    return render_template('auth/login.html', error=error, next=request.args.get('next', ''))



# ==================================================
# PROFILE API
# ==================================================

@app.route("/api/profile/username", methods=["POST"])
@login_required
def api_update_username():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    new_username = data.get("username", "").strip()
    if not new_username or len(new_username) < 2:
        return jsonify({"success": False, "error": "Username minimal 2 karakter"}), 400
    user.username = new_username
    with db_lock:
        db.session.commit()
    return jsonify({"success": True, "username": new_username})


@app.route("/api/profile/password", methods=["POST"])
@login_required
def api_update_password():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    old_pw = data.get("old_password", "")
    new_pw = data.get("new_password", "")
    if not user.check_password(old_pw):
        return jsonify({"success": False, "error": "Password lama salah"}), 401
    if len(new_pw) < 4:
        return jsonify({"success": False, "error": "Password minimal 4 karakter"}), 400
    user.set_password(new_pw)
    with db_lock:
        db.session.commit()
    return jsonify({"success": True})


@app.route("/api/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized", "success": False}), 401

    if "avatar" not in request.files:
        return jsonify({"error": "Tidak ada file", "success": False}), 400

    file = request.files["avatar"]
    if file.filename == "":
        return jsonify({"error": "File kosong", "success": False}), 400

    allowed = {"png", "jpg", "jpeg", "gif", "webp"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        return jsonify({"error": "Format tidak didukung", "success": False}), 400

    import uuid
    filename = f"avatar_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_dir = os.path.join(app.static_folder, "uploads", "avatars")
    os.makedirs(upload_dir, exist_ok=True)

    if user.avatar_url:
        old_path = os.path.join(app.static_folder, user.avatar_url.lstrip("/static/"))
        if os.path.exists(old_path):
            os.remove(old_path)

    file.save(os.path.join(upload_dir, filename))
    avatar_url = f"/static/uploads/avatars/{filename}"
    user.avatar_url = avatar_url
    with db_lock:
        db.session.commit()
    session["avatar_url"] = avatar_url
    return jsonify({"success": True, "avatar_url": avatar_url})


@app.route('/logout')
@login_required
def logout():
    session.clear()
    response = make_response(redirect('/login'))
    clear_auth_cookie(response)
    return response


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """API login - returns JWT in JSON body."""
    data = request.get_json(silent=True) or request.form
    username = data.get('username', 'admin')
    password = data.get('password', '')
    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({'success': False, 'error': 'Password salah'}), 401
    from datetime import datetime, timezone
    user.last_login = datetime.now(timezone.utc)
    with db_lock:
        db.session.commit()
    token = generate_token(user.id, user.username)
    return jsonify({'success': True, 'token': token, 'user': user.to_dict()})


@app.route('/api/auth/me')
@login_required
def api_auth_me():
    user = get_current_user()
    return jsonify({'success': True, 'data': user.to_dict()})


@app.route('/')
@login_required
def dashboard():

    today = datetime.now(timezone).date()

    selected_date = request.args.get('date')

    if selected_date:
        selected_date = datetime.strptime(
            selected_date,
            '%Y-%m-%d'
        ).date()
    else:
        selected_date = today

    selected_month = request.args.get(
        'month',
        selected_date.month,
        type=int
    )

    selected_year = request.args.get(
        'year',
        selected_date.year,
        type=int
    )

    month_matrix = calendar.monthcalendar(
        selected_year,
        selected_month
    )

    month_name = calendar.month_name[
        selected_month
    ]

    prev_month = selected_month - 1
    prev_year = selected_year

    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = selected_month + 1
    next_year = selected_year

    if next_month > 12:
        next_month = 1
        next_year += 1

    start_of_date = datetime.combine(selected_date, datetime.min.time())
    next_day = start_of_date + timedelta(days=1)

    logs = TrashLog.query.filter(
        TrashLog.created_at >= start_of_date,
        TrashLog.created_at < next_day
    ).order_by(
        TrashLog.created_at.desc()
    ).all()

    total_trash = len(logs)

    medis_count = len([
        log for log in logs
        if log.kategori == 'Medical'
    ])

    non_medis_count = len([
        log for log in logs
        if log.kategori == 'Non Medical'
    ])

    # ==========================================
    # PAGE TITLE
    # ==========================================

    if selected_date == today:
        page_title = "Today Trash Statistics"
    else:
        page_title = selected_date.strftime(
            "Trash Statistics - %d %B %Y"
        )

    # ==========================================
    # CHART DATA
    # ==========================================

    first_day = datetime(selected_year, selected_month, 1)
    last_day_num = calendar.monthrange(selected_year, selected_month)[1]
    last_day = datetime(selected_year, selected_month, last_day_num, 23, 59, 59)

    chart_result = db.session.query(
        db.func.date(TrashLog.created_at),
        TrashLog.kategori,
        db.func.count(TrashLog.id)
    ).filter(
        TrashLog.created_at >= first_day,
        TrashLog.created_at <= last_day
    ).group_by(
        db.func.date(TrashLog.created_at),
        TrashLog.kategori
    ).all()

    chart_data = {}

    for tanggal, kategori, jumlah in chart_result:
        tanggal = tanggal.strftime('%d %b')
        if tanggal not in chart_data:
            chart_data[tanggal] = {
                'Medical':0,
                'Non Medical':0
            }
        chart_data[tanggal][kategori] = jumlah

    chart_labels = list(chart_data.keys())

    medical_series = [
        chart_data[x]['Medical']
        for x in chart_labels
    ]

    non_medical_series = [
        chart_data[x]['Non Medical']
        for x in chart_labels
    ]

    hour_labels = [f"{jam:02d}:00" for jam in range(24)]
    medical_hourly = [0] * 24
    non_medical_hourly = [0] * 24

    # Gunakan data logs yang sudah diambil di atas (performa jauh lebih cepat)
    logs_today = logs

    for log in logs_today:
        if not log.created_at:
            continue
        
        # Konversi created_at (UTC) ke WITA sebelum mengambil jamnya
        dt = log.created_at
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt).astimezone(WITA)
        else:
            dt = dt.astimezone(WITA)
            
        jam = dt.hour
        if log.kategori == 'Medical':
            medical_hourly[jam] += 1
        elif log.kategori == 'Non Medical':
            non_medical_hourly[jam] += 1

    # ==========================================
    # BIN STATUS (capacity, weight, fill %)
    # ==========================================
    bin_status = BinStatus.query.get(1)
    if not bin_status:
        with db_lock:
            bin_status = BinStatus(id=1)
            db.session.add(bin_status)
            db.session.commit()

    BIN_EMPTY_CM = 60.0
    BIN_FULL_CM = 30.0

    def fill_pct(jarak_cm):
        if jarak_cm is None or jarak_cm <= 0:
            return 0
        pct = ((BIN_EMPTY_CM - jarak_cm) / (BIN_EMPTY_CM - BIN_FULL_CM)) * 100
        return round(max(0, min(100, pct)), 1)

    bin_capacity = {
        "medis": {
            "jarak_cm": float(bin_status.jarak_medis or 0),
            "status": bin_status.status_medis or "Kosong",
            "berat_g": float(bin_status.berat_medis or 0),
            "fill_pct": fill_pct(bin_status.jarak_medis),
            "updated_at": bin_status.updated_at,
        },
        "non_medis": {
            "jarak_cm": float(bin_status.jarak_non_medis or 0),
            "status": bin_status.status_non_medis or "Kosong",
            "berat_g": float(bin_status.berat_non_medis or 0),
            "fill_pct": fill_pct(bin_status.jarak_non_medis),
            "updated_at": bin_status.updated_at,
        },
    }

    return render_template(
        'dashboard/index.html',
        chart_labels=chart_labels,
        medical_series=medical_series,
        non_medical_series=non_medical_series,
        hour_labels=hour_labels,
        medical_hourly=medical_hourly,
        non_medical_hourly=non_medical_hourly,
        logs=logs,
        today=today,
        selected_date=selected_date,
        total_trash=total_trash,
        medis_count=medis_count,
        non_medis_count=non_medis_count,
        month_matrix=month_matrix,
        month_name=month_name,                                              
        selected_month=selected_month,
        selected_year=selected_year,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        page_title=page_title,
        bin_capacity=bin_capacity
    )

# ==================================================
# ANALYTICS
# ==================================================

@app.route('/analytics')
@login_required
def analytics():

    today = datetime.now(timezone)
    start_date_str = request.args.get("start_date", "").strip()
    end_date_str = request.args.get("end_date", "").strip()
    analytics_filters = {"start_date": start_date_str, "end_date": end_date_str}

    btn = request.args.get("btn", "")
    is_all_time = btn == "semua" or (not btn and not start_date_str and not end_date_str)

    # Parse date range
    if start_date_str and end_date_str:
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone)
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone, hour=23, minute=59, second=59)
        except ValueError:
            start_dt = today - timedelta(days=6)
            start_dt = start_dt.replace(hour=0, minute=0, second=0)
            end_dt = today
    elif is_all_time:
        # Chart display limits to last 30 days so it doesn't freeze the browser
        start_dt = today - timedelta(days=30)
        start_dt = start_dt.replace(hour=0, minute=0, second=0)
        end_dt = today
    else:
        # Default: last 7 days including today (for other buttons if any)
        start_dt = today - timedelta(days=6)
        start_dt = start_dt.replace(hour=0, minute=0, second=0)
        end_dt = today

    # Base query for Totals (cards)
    if is_all_time:
        base_query = TrashLog.query
    else:
        base_query = TrashLog.query.filter(
            TrashLog.created_at >= start_dt,
            TrashLog.created_at <= end_dt
        )

    # Total filtered
    total_filtered = base_query.count()

    # Medical & Non-Medical counts (filtered)
    medical_count = base_query.filter(TrashLog.kategori == "Medical").count()
    non_medical_count = base_query.filter(TrashLog.kategori == "Non Medical").count()

    # Today count (always today, regardless of filter)
    today_count = TrashLog.query.filter(
        db.func.date(TrashLog.created_at) == today.date()
    ).count()

    # Avg confidence (filtered)
    if is_all_time:
        avg_row = db.session.query(db.func.avg(TrashLog.confidence)).scalar()
    else:
        avg_row = db.session.query(db.func.avg(TrashLog.confidence)).filter(
            TrashLog.created_at >= start_dt,
            TrashLog.created_at <= end_dt
        ).scalar()
        
    avg_confidence = float(avg_row) if avg_row else 0.0

    # ==========================
    # CHART — adapts to date range
    # ==========================
    labels = []
    medical_series = []
    non_medical_series = []

    num_days = (end_dt.date() - start_dt.date()).days + 1
    # Cap at 31 days to avoid too many bars
    if num_days > 31:
        num_days = 31

    for i in range(num_days):
        day = start_dt + timedelta(days=i)
        labels.append(day.strftime("%d %b"))

        medis = TrashLog.query.filter(
            db.func.date(TrashLog.created_at) == day.date(),
            TrashLog.kategori == "Medical"
        ).count()

        nonmedis = TrashLog.query.filter(
            db.func.date(TrashLog.created_at) == day.date(),
            TrashLog.kategori == "Non Medical"
        ).count()

        medical_series.append(medis)
        non_medical_series.append(nonmedis)

    # Latest logs (filtered by date range)
    latest_logs = base_query.order_by(
        TrashLog.created_at.desc()
    ).limit(15).all()

    # Current bin weight per kategori
    bin_status_anal = BinStatus.query.get(1)
    berat_per_kategori = {}
    if bin_status_anal:
        berat_per_kategori = {
            "Medical": float(bin_status_anal.berat_medis or 0),
            "Non Medical": float(bin_status_anal.berat_non_medis or 0),
        }

    # Quick filter dates
    week_start_str = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
    month_start_str = today.replace(day=1).strftime('%Y-%m-%d')
    today_str = today.strftime('%Y-%m-%d')

    return render_template(
        'analytics/index.html',
        today=today,
        today_str=today_str,
        week_start=week_start_str,
        month_start=month_start_str,
        total_filtered=total_filtered,
        medical_count=medical_count,
        non_medical_count=non_medical_count,
        today_count=today_count,
        avg_confidence=avg_confidence,
        labels=labels,
        medical_series=medical_series,
        non_medical_series=non_medical_series,
        latest_logs=latest_logs,
        berat_per_kategori=berat_per_kategori,
        analytics_filters=analytics_filters,
        num_days=num_days
    )

# ==================================================
# DEVICES
# ==================================================

@app.route('/devices')
@login_required
def devices_page():

    with db_lock:
        devices_db = DeviceLog.query.all()
    pi_off = any(d.nama_perangkat == "Sensor Raspberry Pi" and d.status == "OFF" for d in devices_db)

    class DeviceWrapper:
        def __init__(self, d):
            self.id = d.id
            self.nama_perangkat = d.nama_perangkat
            self.status = "OFF" if (pi_off and d.nama_perangkat != "Sensor Raspberry Pi") else d.status
            self.created_at = d.created_at
            self.pesan = getattr(d, 'pesan', '')
            self.variant = getattr(d, 'variant', None)
            
    devices = [DeviceWrapper(d) for d in devices_db]

    error_devices = [
        d for d in devices
        if d.status == "ERROR"
    ]

    return render_template(
        "devices/index.html",
        devices=devices,
        error_devices=error_devices
    )

@app.route("/api/device/status", methods=["POST"])
def update_device():
    try:
        data = request.get_json()
        
        if not data or "device" not in data:
            return jsonify(success=False, message="Data tidak lengkap"), 400

        with db_lock:
            device = DeviceLog.query.filter_by(
                nama_perangkat=data["device"]
            ).first()

            if device:
                device.status = data["status"]
                device.last_seen = datetime.now()
                db.session.commit()
                return jsonify(success=True)
            
        return jsonify(success=False, message="Device tidak ditemukan"), 404
    except Exception as e:
        db.session.rollback()
        print(f"Error di update_device: {str(e)}")
        return jsonify(success=False, error=str(e)), 500

@app.route('/device/toggle/<int:device_id>', methods=["POST"])
def toggle_device(device_id):

    with db_lock:
        device = DeviceLog.query.get(device_id)

        if device:
            if device.status == "ON":
                device.status = "OFF"
            else:
                device.status = "ON"
            db.session.commit()

    return redirect(url_for("devices_page"))

@app.route("/api/device")
def device_api():
    try:
        with db_lock:
            devices = DeviceLog.query.all()
            
            device_list = [
                {
                    "id": d.id,
                    "device": d.nama_perangkat,
                    "status": d.status
                }
                for d in devices
            ]
        
        # Diubah menjadi format Dictionary agar script poller di Pi tidak crash '.get'
        return jsonify({"success": True, "devices": device_list})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/weight", methods=["POST"])
def api_weight():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400
            
        with db_lock:
            status = BinStatus.query.get(1)
            if not status:
                status = BinStatus(id=1)
                db.session.add(status)
                
            status.berat_medis = data.get("medis", 0)
            status.berat_non_medis = data.get("non_medis", 0)

            db.session.commit()

        # Emit websocket event untuk real-time berat update
        try:
            socketio.emit("bin_update", {
                "medis": {
                    "jarak_cm": float(status.jarak_medis or 0),
                    "status": status.status_medis or "Kosong",
                    "berat_g": float(status.berat_medis or 0),
                    "updated_at": status.updated_at.strftime("%H:%M:%S") if status.updated_at else None,
                },
                "non_medis": {
                    "jarak_cm": float(status.jarak_non_medis or 0),
                    "status": status.status_non_medis or "Kosong",
                    "berat_g": float(status.berat_non_medis or 0),
                    "updated_at": status.updated_at.strftime("%H:%M:%S") if status.updated_at else None,
                },
            })
        except Exception as e:
            print(f"[WS-EMIT bin_update] Gagal: {e}")

        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        print(f"Error di api_weight: {str(e)}")
        return jsonify(success=False, error=str(e)), 500

@app.route("/api/capacity", methods=["POST"])
def api_capacity():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, message="No data received"), 400
            
        with db_lock:
            status = BinStatus.query.get(1)
            if not status:
                status = BinStatus(id=1)
                db.session.add(status)
                
            status.jarak_medis = data["medis"]["jarak"]
            status.status_medis = data["medis"]["status"]
            status.jarak_non_medis = data["non_medis"]["jarak"]
            status.status_non_medis = data["non_medis"]["status"]

            db.session.commit()

        # Emit websocket event untuk real-time UI update
        BIN_EMPTY_CM = 60.0
        BIN_FULL_CM = 30.0
        def fp(j):
            if not j or j <= 0: return 0
            return round(max(0, min(100, ((BIN_EMPTY_CM - j) / (BIN_EMPTY_CM - BIN_FULL_CM)) * 100)), 1)

        try:
            socketio.emit("bin_update", {
                "medis": {
                    "jarak_cm": float(status.jarak_medis or 0),
                    "status": status.status_medis or "Kosong",
                    "berat_g": float(status.berat_medis or 0),
                    "fill_pct": fp(status.jarak_medis),
                    "updated_at": status.updated_at.strftime("%H:%M:%S") if status.updated_at else None,
                },
                "non_medis": {
                    "jarak_cm": float(status.jarak_non_medis or 0),
                    "status": status.status_non_medis or "Kosong",
                    "berat_g": float(status.berat_non_medis or 0),
                    "fill_pct": fp(status.jarak_non_medis),
                    "updated_at": status.updated_at.strftime("%H:%M:%S") if status.updated_at else None,
                },
            })
        except Exception as e:
            print(f"[WS-EMIT bin_update] Gagal: {e}")

        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        print(f"Error di api_capacity: {str(e)}")
        return jsonify(success=False, error=str(e)), 500   

@app.route("/api/trash_log/<int:log_id>")
def api_trash_log(log_id):
    """Endpoint untuk Flowbite modal detail trash log."""
    log = TrashLog.query.get(log_id)
    if not log:
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({
        "success": True,
        "data": {
            "id": log.id,
            "kategori": log.kategori,
            "jenis_sampah": log.jenis_sampah,
            "confidence": float(log.confidence or 0),
            "image_path": log.image_path,
            "created_at": log.created_at.strftime("%d %b %Y %H:%M:%S") if log.created_at else None,
        }
    })


@app.route("/api/bin_status")
def api_bin_status():
    """Endpoint untuk real-time update kapasitas & berat via polling/websocket."""
    bin_status = BinStatus.query.get(1)
    if not bin_status:
        return jsonify({"success": False, "error": "BinStatus not initialized"}), 404

    BIN_EMPTY_CM = 30.0
    BIN_FULL_CM = 5.0

    def fill_pct(jarak_cm):
        if jarak_cm is None or jarak_cm <= 0:
            return 0
        pct = ((BIN_EMPTY_CM - jarak_cm) / (BIN_EMPTY_CM - BIN_FULL_CM)) * 100
        return round(max(0, min(100, pct)), 1)

    payload = {
        "success": True,
        "medis": {
            "jarak_cm": float(bin_status.jarak_medis or 0),
            "status": bin_status.status_medis or "Kosong",
            "berat_g": float(bin_status.berat_medis or 0),
            "fill_pct": fill_pct(bin_status.jarak_medis),
            "updated_at": bin_status.updated_at.strftime("%H:%M:%S") if bin_status.updated_at else None,
        },
        "non_medis": {
            "jarak_cm": float(bin_status.jarak_non_medis or 0),
            "status": bin_status.status_non_medis or "Kosong",
            "berat_g": float(bin_status.berat_non_medis or 0),
            "fill_pct": fill_pct(bin_status.jarak_non_medis),
            "updated_at": bin_status.updated_at.strftime("%H:%M:%S") if bin_status.updated_at else None,
        },
    }

    return jsonify(payload)

# ==================================================
# HISTORY
# ==================================================

@app.route('/history')
@login_required
def history():

    kategori = request.args.get('kategori', '').strip()
    tanggal = request.args.get('tanggal', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    q = request.args.get('q', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    per_page = 15

    query = TrashLog.query

    if kategori and kategori not in ("", "Semua"):
        query = query.filter(TrashLog.kategori == kategori)
    if tanggal:
        query = query.filter(db.func.date(TrashLog.created_at) == tanggal)
    if start_date:
        query = query.filter(db.func.date(TrashLog.created_at) >= start_date)
    if end_date:
        query = query.filter(db.func.date(TrashLog.created_at) <= end_date)
    if q:
        like = f"%{q}%"
        query = query.filter(TrashLog.kategori.ilike(like))

    query = query.order_by(TrashLog.created_at.desc())
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    logs = query.offset((page - 1) * per_page).limit(per_page).all()

    # Current bin weight (per-kategori) - dipakai sebagai "deteksi berat"
    # karena belum ada berat per-trash, pakai current bin weight sebagai proxy
    bin_status = BinStatus.query.get(1)
    berat_per_kategori = {}
    if bin_status:
        berat_per_kategori = {
            "Medical": float(bin_status.berat_medis or 0),
            "Non Medical": float(bin_status.berat_non_medis or 0),
        }

    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < pages else None,
    }

    return render_template(
        'history/index.html',
        logs=logs,
        berat_per_kategori=berat_per_kategori,
        pagination=pagination,
        filters={
            "kategori": kategori,
            "tanggal": tanggal,
            "start_date": start_date,
            "end_date": end_date,
            "q": q,
        }
    )


# ==================================================
# NOTIFICATIONS (bell icon di navbar)
# ==================================================

@app.route("/api/notifications")
def api_notifications():
    """List notifikasi: bin penuh, sensor error."""
    # Load iot_config untuk threshold ultrasonic (or hardcoded fallback)
    try:
        with open("/var/www/Raabbar/iot_config.json") as f:
            cfg = json.load(f)
        THRESH_PENUH = cfg["ultrasonic"]["threshold_penuh_cm"]      # e.g. 30 -> dianggap 100%
        THRESH_HAMPIR = cfg["ultrasonic"]["threshold_hampir_penuh_cm"]  # e.g. 50 -> dianggap 70%
    except Exception:
        THRESH_PENUH = 30
        THRESH_HAMPIR = 50

    def compute_fill_pct(jarak):
        """Hitung fill% dari jarak sensor. jarak<=THRESH_PENUH = 100%, >=THRESH_HAMPIR = 0%."""
        if jarak is None or jarak <= 0:
            return 0
        if jarak <= THRESH_PENUH:
            return 100.0
        if jarak >= THRESH_HAMPIR:
            return 0.0
        # Linear interpolation
        return round((THRESH_HAMPIR - jarak) / (THRESH_HAMPIR - THRESH_PENUH) * 100, 1)

    status = BinStatus.query.get(1)
    notifs = []

    # 1) Bin medis
    if status:
        pct_m = compute_fill_pct(status.jarak_medis)
        pct_n = compute_fill_pct(status.jarak_non_medis)
        if pct_m >= 80:
            notifs.append({
                "id": "bin_medis_full",
                "type": "warning",
                "title": "Tempat Sampah Medis Hampir Penuh",
                "message": f"Kapasitas {pct_m:.0f}% - segera dikosongkan",
                "timestamp": None,
            })
        if pct_n >= 80:
            notifs.append({
                "id": "bin_nonmedis_full",
                "type": "warning",
                "title": "Tempat Sampah Non-Medis Hampir Penuh",
                "message": f"Kapasitas {pct_n:.0f}% - segera dikosongkan",
                "timestamp": None,
            })

    # 2) Device error logs (15 menit terakhir)
    from datetime import timedelta
    cutoff = datetime.now(timezone) - timedelta(minutes=15)
    err_logs = DeviceLog.query.filter(
        DeviceLog.status == "ERROR",
        DeviceLog.created_at >= cutoff
    ).order_by(DeviceLog.created_at.desc()).limit(5).all()
    for el in err_logs:
        notifs.append({
            "id": f"device_{el.id}",
            "type": "error",
            "title": f"Device Error: {el.nama_perangkat}",
            "message": el.pesan or "Perangkat tidak merespon",
            "timestamp": el.created_at.isoformat() if el.created_at else None if el.timestamp else None,
        })

    return jsonify({
        "success": True,
        "count": len(notifs),
        "data": notifs
    })

@app.route("/api/trash", methods=["POST"])
def api_trash():

    print("===== API TRASH DIPANGGIL =====")

    kategori = request.form.get("kategori")
    jenis = request.form.get("jenis_sampah")
    confidence = float(request.form.get("confidence"))

    image = request.files["image"]

    print(kategori)
    print(jenis)
    print(confidence)
    print(image.filename)

    filename = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f.jpg"
    )

    filename = secure_filename(filename)

    image.save(
        os.path.join(
            UPLOAD_FOLDER,
            filename
        )
    )

    image_path = f"uploads/history/{filename}"

    log = TrashLog(
        kategori=kategori,
        jenis_sampah=jenis,
        confidence=confidence,
        image_path=image_path
    )

    with db_lock:
        db.session.add(log)
        db.session.commit()

    socketio.emit(
        "new_trash",
        {
            "kategori":kategori,
            "jenis":jenis,
            "confidence":confidence,
            "image":image_path,
            "time":log.created_at.strftime("%H:%M"),
            "date":log.created_at.strftime("%d %b %Y")
        }
    )

    return jsonify({
        "success":True
    })

# ==================================================
# SERVE ASSETS LOCALLY
# ==================================================
from flask import send_from_directory

@app.route('/asset/<path:filename>')
def serve_asset(filename):
    return send_from_directory(os.path.join(app.root_path, 'asset'), filename)

# ==================================================
# RUN
# ==================================================

if __name__ == '__main__':
    socketio.run(
        app,
        host='0.0.0.0',
        port=8003,
        debug=True
    )
