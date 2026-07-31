import requests
import time

url = "https://wastescan.site/api/capacity"
payload = {
    "medis": {"jarak": 63.5, "status": "Kosong"},
    "non_medis": {"jarak": 63.7, "status": "Kosong"}
}
try:
    print(f"Sending POST to {url}...")
    start_time = time.time()
    response = requests.post(url, json=payload, timeout=20.0)
    print(f"Time taken: {time.time() - start_time:.2f}s")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
