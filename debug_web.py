
import sys
import os
import traceback
sys.path.append(os.getcwd())

try:
    from web_dashboard.app import app
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
    traceback.print_exc()
    sys.exit(1)

from fastapi.testclient import TestClient

client = TestClient(app)

try:
    response = client.get("/")
    print(f"Status Code: {response.status_code}")
    if response.status_code == 500:
        print("Got 500 Error")
        print(response.text)
except Exception as e:
    print("Request failed with exception:")
    traceback.print_exc()
