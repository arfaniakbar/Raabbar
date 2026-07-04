# 🚀 PANDUAN LENGKAP: Update Project Raabbar dari GitHub ke VPS

> **Project:** Raabbar (Flask App)  
> **VPS IP:** 72.62.124.168  
> **Port:** 8003  
> **Path:** `/var/www/Raabbar`

---

## 📋 DAFTAR ISI
1. [Persiapan Awal](#1-persiapan-awal)
2. [Login ke VPS via SSH](#2-login-ke-vps-via-ssh)
3. [Git Pull dari GitHub](#3-git-pull-dari-github)
4. [Install Dependencies Baru (Jika Ada)](#4-install-dependencies-baru-jika-ada)
5. [Restart Service Raabbar](#5-restart-service-raabbar)
6. [Verifikasi](#6-verifikasi)
7. [Troubleshooting](#7-troubleshooting)
8. [Quick Reference](#8-quick-reference)

---

## 1. PERSIAPAN AWAL

### Yang Dibutuhkan:
- ✅ Komputer/Laptop dengan terminal/command prompt
- ✅ SSH client (PuTTY untuk Windows, Terminal untuk Mac/Linux)
- ✅ Kredensial VPS:
  - **IP:** `72.62.124.168`
  - **Username:** `root`
  - **Password:** (password VPS kamu)
- ✅ Repository GitHub sudah di-push dari lokal

### Cek Repository GitHub:
Pastikan kode terbaru sudah di-push ke:
```
https://github.com/arfaniakbar/Raabbar
```

---

## 2. LOGIN KE VPS VIA SSH

### Opsi A: Windows (PuTTY)

1. **Download & Install PuTTY** (jika belum punya)
   - Download: https://www.putty.org/

2. **Buka PuTTY** dan isi:
   - **Host Name (or IP address):** `72.62.124.168`
   - **Port:** `22`
   - **Connection type:** SSH

3. **Klik "Open"**

4. **Login:**
   ```
   login as: root
   root@72.62.124.168's password: [masukkan password]
   ```

5. **Kalau muncul warning "Security Alert"** → klik **Yes**

### Opsi B: Mac/Linux/Windows 10+ (Terminal)

1. **Buka Terminal** (Mac) atau **Command Prompt/PowerShell** (Windows)

2. **Ketik command:**
   ```bash
   ssh root@72.62.124.168
   ```

3. **Masukkan password** saat diminta:
   ```
   root@72.62.124.168's password: [masukkan password]
   ```
   > ⚠️ Password tidak akan terlihat saat diketik (normal)

4. **Kalau muncul warning:**
   ```
   Are you sure you want to continue connecting (yes/no)?
   ```
   Ketik: `yes` lalu Enter

### ✅ Berhasil Login Jika Muncul:
```
Welcome to Ubuntu 24.04 LTS
root@srv1675533:~#
```

---

## 3. GIT PULL DARI GITHUB

### Step 3.1: Pindah ke Directory Project

```bash
cd /var/www/Raabbar
```

**Cek posisi sekarang:**
```bash
pwd
```
Harusnya muncul: `/var/www/Raabbar`

### Step 3.2: Cek Status Git

```bash
git status
```

**Output normal:**
```
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
```

### Step 3.3: Git Pull

```bash
git pull origin main
```

**Atau kalau branch kamu bukan "main" (misalnya "master"):**
```bash
git pull origin master
```

### ✅ Output Berhasil:
```
From https://github.com/arfaniakbar/Raabbar
 * branch            main       -> FETCH_HEAD
Updating abc1234..def5678
Fast-forward
 app.py         | 10 +++++++---
 config.py      |  2 +-
 2 files changed, 8 insertions(+), 4 deletions(-)
```

### ❌ Kalau Ada Error "CONFLICT":

Kalau muncul error seperti:
```
CONFLICT (content): Merge conflict in app.py
Automatic merge failed; fix conflicts and then commit the result.
```

**Solusi (Force Update - HAPUS perubahan di VPS):**
```bash
git fetch --all
git reset --hard origin/main
```

> ⚠️ **PERINGATAN:** Command di atas akan **MENGHAPUS** semua perubahan lokal di VPS dan menggantinya dengan yang ada di GitHub. Pastikan tidak ada perubahan penting di VPS yang belum di-push!

### ❌ Kalau Error "Permission denied":

```bash
sudo git pull origin main
```

---

## 4. INSTALL DEPENDENCIES BARU (JIKA ADA)

### Step 4.1: Cek Apakah Ada requirements.txt Baru

```bash
cat requirements.txt
```

### Step 4.2: Install Dependencies (Jika requirements.txt Tidak Kosong)

```bash
source venv/bin/activate
pip install -r requirements.txt
```

**Atau kalau requirements.txt kosong** (seperti project ini), install manual:
```bash
source venv/bin/activate
pip install flask flask-sqlalchemy pymysql flask-socketio pytz gunicorn gevent gevent-websocket
```

### ✅ Output Berhasil:
```
Successfully installed flask-3.1.3 flask-sqlalchemy-3.1.1 ...
```

### Step 4.3: Keluar dari Virtual Environment

```bash
deactivate
```

---

## 5. RESTART SERVICE RAABBAR

### Step 5.1: Restart Service

```bash
sudo systemctl restart raabbar
```

### Step 5.2: Cek Status Service

```bash
sudo systemctl status raabbar
```

### ✅ Output Berhasil (Harus "active (running)"):
```
● raabbar.service - Raabbar Flask App
     Loaded: loaded (/etc/systemd/system/raabbar.service; enabled)
     Active: active (running) since Mon 2026-06-22 18:05:19 WITA; 5min ago
   Main PID: 193324 (gunicorn)
      Tasks: 6 (limit: 9483)
     Memory: 120.1M
```

> **PENTING:** Pastikan status = `active (running)` dan bukan `failed` atau `inactive`

### Step 5.3: Restart Nginx (Opsional, Jika Ada Perubahan Config)

```bash
sudo systemctl restart nginx
```

---

## 6. VERIFIKASI

### Step 6.1: Cek Log (Pastikan Tidak Ada Error)

```bash
sudo journalctl -u raabbar -n 20 --no-pager
```

**Output normal** (hanya warning, bukan error):
```
Jun 22 18:05:20 srv1675533 gunicorn[193326]: EventletDeprecationWarning...
```

**Kalau ada ERROR:**
```
Traceback (most recent call last):
  File "app.py", line 10, in <module>
ModuleNotFoundError: No module named 'xxx'
```
→ Kembali ke Step 4 dan install dependencies yang kurang

### Step 6.2: Test Akses dari Browser

Buka browser dan akses:
```
http://72.62.124.168:8003
```

**Harusnya muncul:** Dashboard Raabbar

### Step 6.3: Test Semua Halaman

- Dashboard: `http://72.62.124.168:8003/`
- Analytics: `http://72.62.124.168:8003/analytics`
- Devices: `http://72.62.124.168:8003/devices`
- History: `http://72.62.124.168:8003/history`

Semua harus return **HTTP 200** (halaman muncul, bukan error)

---

## 7. TROUBLESHOOTING

### Problem 1: Service Gagal Start (Status = "failed")

**Cek error detail:**
```bash
sudo journalctl -u raabbar -n 50 --no-pager
```

**Common causes:**
- Missing dependencies → Install dependencies (Step 4)
- Database connection error → Cek MySQL running: `systemctl status mysql`
- Port already in use → Kill process: `sudo fuser -k 8000/tcp`

**Fix:**
```bash
sudo systemctl stop raabbar
sudo fuser -k 8000/tcp
sudo systemctl start raabbar
```

### Problem 2: Halaman Blank/Error 500

**Cek log:**
```bash
sudo journalctl -u raabbar -f
```
(Lalu refresh browser, lihat error yang muncul)

**Common causes:**
- Database table tidak ada → Jalankan app manual sekali:
  ```bash
  cd /var/www/Raabbar
  source venv/bin/activate
  python app.py
  ```
  (Ctrl+C untuk stop, lalu restart service)

### Problem 3: Git Pull Error "Not a git repository"

**Fix:**
```bash
cd /var/www/Raabbar
git init
git remote add origin https://github.com/arfaniakbar/Raabbar.git
git fetch
git reset --hard origin/main
```

### Problem 4: Permission Denied saat Git Pull

**Fix:**
```bash
sudo chown -R root:root /var/www/Raabbar
sudo chmod -R 755 /var/www/Raabbar
```

### Problem 5: Port 8003 Tidak Bisa Diakses

**Checklist:**
- [ ] Service running? → `systemctl status raabbar`
- [ ] Nginx running? → `systemctl status nginx`
- [ ] Port listening? → `ss -tlnp | grep 8003`
- [ ] Firewall Hostinger sudah buka port 8003?
- [ ] Akses dari data seluler (bukan WiFi)?

---

## 8. QUICK REFERENCE

### 🚀 Command Lengkap (Copy-Paste Ini)

```bash
# 1. Login ke VPS
ssh root@72.62.124.168

# 2. Pindah ke directory
cd /var/www/Raabbar

# 3. Git pull
git pull origin main

# 4. (Opsional) Install dependencies
source venv/bin/activate
pip install -r requirements.txt
deactivate

# 5. Restart service
sudo systemctl restart raabbar

# 6. Cek status
sudo systemctl status raabbar

# 7. Cek log (kalau ada masalah)
sudo journalctl -u raabbar -n 30 --no-pager

# 8. Selesai! Buka browser: http://72.62.124.168:8003
```

### 🔥 Force Update (Kalau Ada Conflict)

```bash
cd /var/www/Raabbar
git fetch --all
git reset --hard origin/main
sudo systemctl restart raabbar
```

### 📊 Monitoring Commands

```bash
# Cek status service
systemctl status raabbar

# Live log (real-time)
journalctl -u raabbar -f

# Cek port listening
ss -tlnp | grep 8003

# Cek Nginx status
systemctl status nginx

# Restart semua (Nginx + Raabbar)
sudo systemctl restart nginx && sudo systemctl restart raabbar
```

---

## 📝 CATATAN PENTING

1. **Selalu backup sebelum update besar:**
   ```bash
   cd /var/www
   tar -czf Raabbar-backup-$(date +%Y%m%d).tar.gz Raabbar/
   ```

2. **Jangan edit file langsung di VPS** — edit di lokal, push ke GitHub, lalu pull di VPS

3. **Kalau ada perubahan database schema** (model baru), tabel akan auto-create saat app start

4. **Port 8003 khusus untuk Raabbar** — jangan pakai untuk service lain

5. **Service auto-start** — kalau VPS reboot, Raabbar otomatis jalan lagi

---

## ✅ CHECKLIST UPDATE BERHASIL

- [ ] Login ke VPS berhasil
- [ ] `git pull` berhasil (no error)
- [ ] Dependencies terinstall (jika ada yang baru)
- [ ] `systemctl restart raabbar` berhasil
- [ ] `systemctl status raabbar` = **active (running)**
- [ ] Browser bisa buka `http://72.62.124.168:8003`
- [ ] Semua halaman (Dashboard, Analytics, Devices, History) bisa diakses

---

**Last Updated:** 22 Juni 2026  
**Author:** Deployment Guide untuk Project Raabbar
