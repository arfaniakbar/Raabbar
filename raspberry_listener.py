import time
import requests

API = "http://192.168.1.10:5000/api/device"

while True:

    response = requests.get(API).json()
    devices = response.get("devices", []) if response.get("success") else []

    for d in devices:

        if d["device"] == "Camera":

            if d["status"] == "ON":
                print("Camera ON")
                # start_camera()

            else:
                print("Camera OFF")
                # stop_camera()

    time.sleep(1)