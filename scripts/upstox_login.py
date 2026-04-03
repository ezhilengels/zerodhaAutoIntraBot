"""
scripts/upstox_login.py
───────────────────────
Helper to generate UPSTOX_ACCESS_TOKEN.
1. Run this script.
2. Visit the printed URL.
3. Login and authorize.
4. Copy the 'code' from the redirected URL (e.g. ?code=XXXXXX).
5. Paste it back here.
"""

import os
import requests
from dotenv import load_dotenv

# Load env variables
load_dotenv()

API_KEY = os.getenv("UPSTOX_API_KEY")
API_SECRET = os.getenv("UPSTOX_API_SECRET")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080/")

def main():
    if not API_KEY or not API_SECRET:
        print("❌ Error: UPSTOX_API_KEY or UPSTOX_API_SECRET not found in .env")
        return

    # 1. Generate Auth URL
    auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={API_KEY}&redirect_uri={REDIRECT_URI}"
    
    print("\n" + "═"*60)
    print("  UPSTOX LOGIN HELPER")
    print("═"*60)
    print(f"1. Open this URL in your browser:\n\n{auth_url}\n")
    print("2. Login and click 'Authorize'.")
    print("3. You will be redirected to a page that fails to load (that's fine).")
    print(f"4. Look at the URL in your browser address bar. It will look like:\n   {REDIRECT_URI}?code=XXXXXX\n")
    
    auth_code = input("5. Paste the 'code' part here: ").strip()
    
    if not auth_code:
        print("❌ Error: No code provided.")
        return

    # 2. Exchange Code for Access Token
    url = "https://api.upstox.com/v2/login/authorization/token"
    data = {
        'code': auth_code,
        'client_id': API_KEY,
        'client_secret': API_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    }
    headers = {'accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'}

    print("\nRequesting Access Token...")
    response = requests.post(url, data=data, headers=headers)
    
    if response.status_code == 200:
        res_data = response.json()
        access_token = res_data.get('access_token')
        print("\n" + "✅ SUCCESS!" + "═"*50)
        print(f"Your Access Token is:\n\n{access_token}\n")
        print("Add this to your .env file as:")
        print(f"UPSTOX_ACCESS_TOKEN={access_token}")
        print("═"*60 + "\n")
    else:
        print(f"❌ Error exchanging code: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    main()
