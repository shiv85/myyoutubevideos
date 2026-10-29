"""Run this ONCE on your own computer to let the bot upload to your channel.

1. pip install google-auth-oauthlib
2. Put the client_secret.json you downloaded from Google Cloud next to this file
3. python get_youtube_token.py
4. A browser opens -> choose the Google account that owns "Shiv Maurya Vlogs" -> Allow
5. Copy the three printed values into GitHub -> Settings -> Secrets and variables -> Actions"""
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
info = json.load(open("client_secret.json"))
info = info.get("installed") or info.get("web")

print("\n==== Add these 3 GitHub secrets ====")
print("YT_CLIENT_ID     =", info["client_id"])
print("YT_CLIENT_SECRET =", info["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
print("====================================\nKeep them private. Never share them with anyone.")
