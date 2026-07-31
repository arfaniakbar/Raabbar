import time
import requests

API = "http://192.168.1.10:5000/api/device"

while True:

    devices = requests.get(API).json()

    for d in devices:

        if d["device"] == "Camera":

            if d["status"] == "ON":
                print("Camera ON")
                # start_camera()

            else:
                print("Camera OFF")
                # stop_camera()

    time.sleep(1)