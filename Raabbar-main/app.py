from flask import Flask
from flask import render_template
from flask import request
from models.bin_status import BinStatus

import os
from flask_socketio import SocketIO
import uuid

from flask import jsonify
from werkzeug.utils import secure_filename
from datetime import datetime
from datetime import timedelta
import calendar

import pytz

from config import Config

from database.db import db
from flask import redirect
from flask import url_for


# ==================================================
# APP
# ==================================================

app = Flask(__name__)

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

socketio = SocketIO(app, async_mode="gevent")

# ==================================================
# TIMEZONE
# ==================================================

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
        "status": "OFF"
    },
    {
        "id": 6,
        "name": "Sensor Ultrasonik Medis",
        "status": "ERROR"
    }
]

# ==================================================
# CREATE TABLE
# ==================================================

from models.sampah import Sampah
from models.berat_sampah import BeratSampah
from models.pengambilan import PengambilanSampah

from models.trash_log import TrashLog
from models.device_log import DeviceLog
from models.history_log import HistoryLog

with app.app_context():
    print(db.metadata.tables.keys())
    db.create_all()

with app.app_context():
    if BinStatus.query.first() is None:
        db.session.add(
            BinStatus(id=1)
        )
        db.session.commit()

    if DeviceLog.query.first() is None:
        for d in devices:
            db.session.add(
                DeviceLog(
                    id=d["id"],
                    nama_perangkat=d["name"],
                    status=d["status"]
                )
            )
        db.session.commit()

# ==================================================
# DASHBOARD
# ==================================================

@app.route('/')
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

    logs = TrashLog.query.filter(
        db.func.date(
            TrashLog.created_at
        ) == selected_date
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

    chart_result = db.session.query(
        db.func.date(TrashLog.created_at),
        TrashLog.kategori,
        db.func.count(TrashLog.id)
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

    hour_labels = []
    medical_hourly = []
    non_medical_hourly = []

    for jam in range(24):
        hour_labels.append(f"{jam:02d}:00")

        medis = TrashLog.query.filter(
            db.func.date(TrashLog.created_at) == selected_date,
            db.func.hour(TrashLog.created_at) == jam,
            TrashLog.kategori == 'Medical'
        ).count()

        non_medis = TrashLog.query.filter(
            db.func.date(TrashLog.created_at) == selected_date,
            db.func.hour(TrashLog.created_at) == jam,
            TrashLog.kategori == 'Non Medical'
        ).count()

        medical_hourly.append(medis)
        non_medical_hourly.append(non_medis)

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
        page_title=page_title
    )

# ==================================================
# ANALYTICS
# ==================================================

@app.route('/analytics')
def analytics():

    today = datetime.now(timezone)

    # awal minggu (Senin)
    start_week = today - timedelta(days=today.weekday())

    # total sampah minggu ini
    total_week = TrashLog.query.filter(
        TrashLog.created_at >= start_week
    ).count()

    # medis & non medis minggu ini
    medical_count = TrashLog.query.filter(
        TrashLog.created_at >= start_week,
        TrashLog.kategori == "Medical"
    ).count()

    # rata-rata per hari minggu ini
    non_medical_count = TrashLog.query.filter(
        TrashLog.created_at >= start_week,
        TrashLog.kategori == "Non Medical"
    ).count()

    avg_daily = round(total_week / 7, 1)

    # contoh error device
    error_count = DeviceLog.query.filter(
        DeviceLog.status == "ERROR"
    ).count()

    # ==========================
    # CHART MINGGUAN
    # ==========================

    labels = []
    medical_series = []
    non_medical_series = []

    for i in range(7):
        day = start_week + timedelta(days=i)
        labels.append(
            day.strftime("%a")
        )

        medis = TrashLog.query.filter(
            db.func.date(
                TrashLog.created_at
            ) == day.date(),
            TrashLog.kategori == "Medical"
        ).count()

        nonmedis = TrashLog.query.filter(
            db.func.date(
                TrashLog.created_at
            ) == day.date(),
            TrashLog.kategori == "Non Medical"
        ).count()

        medical_series.append(medis)
        non_medical_series.append(nonmedis)

    # tabel terbaru
    latest_logs = TrashLog.query.order_by(
        TrashLog.created_at.desc()
    ).limit(10).all()

    return render_template(
        'analytics/index.html',
        total_week=total_week,
        avg_daily=avg_daily,
        error_count=error_count,
        medical_count=medical_count,
        non_medical_count=non_medical_count,
        labels=labels,
        medical_series=medical_series,
        non_medical_series=non_medical_series,
        latest_logs=latest_logs
    )

# ==================================================
# DEVICES
# ==================================================

@app.route('/devices')
def devices_page():

    devices = DeviceLog.query.all()

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
            
        status = BinStatus.query.get(1)
        if not status:
            status = BinStatus(id=1)
            db.session.add(status)
            
        status.berat_medis = data.get("medis", 0)
        status.berat_non_medis = data.get("non_medis", 0)
        
        db.session.commit()
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
            
        status = BinStatus.query.get(1)
        if not status:
            status = BinStatus(id=1)
            db.session.add(status)
            
        status.jarak_medis = data["medis"]["jarak"]
        status.status_medis = data["medis"]["status"]
        status.jarak_non_medis = data["non_medis"]["jarak"]
        status.status_non_medis = data["non_medis"]["status"]
        
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        print(f"Error di api_capacity: {str(e)}")
        return jsonify(success=False, error=str(e)), 500   

# ==================================================
# HISTORY
# ==================================================

@app.route('/history')
def history():

    kategori = request.args.get('kategori')
    tanggal = request.args.get('tanggal')
    jenis = request.args.get('jenis')

    query = TrashLog.query

    if kategori and kategori != "Semua":
        query = query.filter(
            TrashLog.kategori == kategori
        )

    if jenis and jenis != "Semua":
        query = query.filter(
            TrashLog.jenis_sampah == jenis
        )

    if tanggal:
        query = query.filter(
            db.func.date(
                TrashLog.created_at
            ) == tanggal
        )

    logs = query.order_by(
        TrashLog.created_at.desc()
    ).all()

    return render_template(
        'history/index.html',
        logs=logs
    )

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
# RUN
# ==================================================

if __name__ == '__main__':
    socketio.run(
        app,
        host='0.0.0.0',
        port=8003,
        debug=True
    )
