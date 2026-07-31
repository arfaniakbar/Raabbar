import urllib.request
import json
import threading
import time

def req_capacity():
    req=urllib.request.Request('https://wastescan.site/api/capacity', data=json.dumps({'medis': {'jarak': 63.5, 'status': 'Kosong'}, 'non_medis': {'jarak': 63.7, 'status': 'Kosong'}}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    start = time.time()
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f"Capacity ok in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"Capacity err: {e}")

def req_weight():
    # Fix the payload for weight!
    req=urllib.request.Request('https://wastescan.site/api/weight', data=json.dumps({'medis': 0.0, 'non_medis': 0.0}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    start = time.time()
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f"Weight ok in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"Weight err: {e}")

def req_poller():
    # Fix the payload for poller!
    req=urllib.request.Request('https://wastescan.site/api/device', headers={'Content-Type': 'application/json'})
    start = time.time()
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f"Poller ok in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"Poller err: {e}")

threads = [threading.Thread(target=req_capacity), threading.Thread(target=req_weight), threading.Thread(target=req_poller)]
for t in threads: t.start()
for t in threads: t.join()
