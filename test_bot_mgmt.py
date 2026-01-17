import requests
import json

BASE_URL = "http://127.0.0.1:8000/api"

def test_bot_management():
    session = requests.Session()
    
    # 1. Login
    print("Logging in...")
    login_payload = {"username": "admin", "password": "admin123"}
    res = session.post(f"{BASE_URL}/auth/login", json=login_payload)
    if res.status_code != 200:
        print(f"Login failed: {res.text}")
        return
    
    token = res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Login successful.")

    # 2. Add Bot Token
    print("\nAdding Bot Token...")
    token_payload = {"name": "Test Bot", "token": "123456789:ABCDefGhiJklMnoPqrStuVwxYz"}
    res = session.post(f"{BASE_URL}/bot/tokens", json=token_payload, headers=headers)
    if res.status_code != 200:
        print(f"Failed to add token: {res.text}")
    else:
        print("Token added successfully.")

    # 3. Get Bot Tokens
    print("\nFetching Bot Tokens...")
    res = session.get(f"{BASE_URL}/bot/tokens", headers=headers)
    tokens = res.json()
    print(f"Tokens found: {len(tokens)}")
    test_token_id = None
    for t in tokens:
        if t["name"] == "Test Bot":
            test_token_id = t["id"]
            print(f"Found Test Bot ID: {test_token_id}, Masked Token: {t['token']}")
    
    if not test_token_id:
        print("Test Bot not found in list!")
        return

    # 4. Delete Bot Token
    print("\nDeleting Bot Token...")
    res = session.delete(f"{BASE_URL}/bot/tokens/{test_token_id}", headers=headers)
    if res.status_code == 200:
        print("Token deleted successfully.")
    else:
        print(f"Failed to delete token: {res.text}")

    # 5. Add Access
    print("\nAdding Access...")
    access_payload = {"user_id": 987654321, "role": "editor", "name": "Test Editor"}
    res = session.post(f"{BASE_URL}/bot/access", json=access_payload, headers=headers)
    if res.status_code != 200:
        print(f"Failed to add access: {res.text}")
    else:
        print("Access added successfully.")

    # 6. Get Access
    print("\nFetching Access List...")
    res = session.get(f"{BASE_URL}/bot/access", headers=headers)
    access_list = res.json()
    print(f"Access entries found: {len(access_list)}")
    test_access_id = None
    for a in access_list:
        if a["user_id"] == 987654321:
            test_access_id = a["id"]
            print(f"Found Access ID: {test_access_id}, Role: {a['role']}")
    
    if not test_access_id:
        print("Test Access not found!")
        return

    # 7. Delete Access
    print("\nDeleting Access...")
    res = session.delete(f"{BASE_URL}/bot/access/{test_access_id}", headers=headers)
    if res.status_code == 200:
        print("Access deleted successfully.")
    else:
        print(f"Failed to delete access: {res.text}")

if __name__ == "__main__":
    try:
        test_bot_management()
    except Exception as e:
        print(f"An error occurred: {e}")
