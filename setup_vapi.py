"""
One-time (re-runnable) setup script.

What it does, step by step:
  1. Loads all secrets/config from .env
  2. Creates (or updates, if run again) the Vapi assistant using the
     system prompt + tool schema defined in assistant_config.py
  3. Imports your Twilio phone number into Vapi and points it at that
     assistant, so incoming calls to the Twilio number are answered by Vapi.

Run this AFTER webhook_server.py is reachable at a public URL, because the
assistant's tool definition needs a real URL to send order data to. In
production that's the Railway deployment; for local development it's an
ngrok tunnel pointing at webhook_server.py.

Usage:
    python setup_vapi.py
    python setup_vapi.py --webhook-url https://your-app.up.railway.app

Note that --webhook-url wins over WEBHOOK_BASE_URL in .env. Railway's own
environment variables are NOT visible here - this script runs locally, so
the deployed URL has to come from .env or the flag.
"""

import argparse
import json
import os
import sys
from typing import Optional

import requests
from dotenv import load_dotenv

from assistant_config import build_assistant_payload

load_dotenv()

VAPI_PRIVATE_KEY = os.getenv("VAPI_PRIVATE_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

VAPI_BASE_URL = "https://api.vapi.ai"

# Where we remember the assistant/phone-number IDs Vapi gave us, so
# re-running this script updates existing resources instead of duplicating
# them. Plain local files are enough for an MVP — no database needed.
ASSISTANT_ID_FILE = "assistant_id.txt"
PHONE_NUMBER_ID_FILE = "phone_number_id.txt"


def _headers():
    return {
        "Authorization": f"Bearer {VAPI_PRIVATE_KEY}",
        "Content-Type": "application/json",
    }


def _fail(step: str, response: requests.Response):
    """Print the full error so the user can see exactly what Vapi rejected."""
    print(f"\n[ERROR] Step failed: {step}")
    print(f"HTTP status: {response.status_code}")
    try:
        print("Response body:", json.dumps(response.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print("Response body (raw):", response.text)
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--webhook-url",
        default=WEBHOOK_BASE_URL,
        help=(
            "Public base URL of webhook_server.py, e.g. "
            "https://your-app.up.railway.app. Overrides WEBHOOK_BASE_URL "
            "from .env; defaults to it when omitted."
        ),
    )
    return parser.parse_args()


def check_env_vars(webhook_url: Optional[str]):
    """Step 0: make sure required secrets are actually filled in before we call any API."""
    required = {
        "VAPI_PRIVATE_KEY": VAPI_PRIVATE_KEY,
        "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
        "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
        "WEBHOOK_BASE_URL": webhook_url,
    }
    missing = [k for k, v in required.items() if not v or v.startswith("your_") or "your-" in v]
    if missing:
        print("[ERROR] Missing/unfilled required config (from .env, or --webhook-url):")
        for k in missing:
            print(f"  - {k}")
        sys.exit(1)


def create_or_update_assistant(webhook_url: str) -> str:
    """Step 1: create the Vapi assistant (or PATCH it if we've already created one)."""
    payload = build_assistant_payload(webhook_url)

    existing_id = None
    if os.path.exists(ASSISTANT_ID_FILE):
        with open(ASSISTANT_ID_FILE, "r") as f:
            existing_id = f.read().strip() or None

    if existing_id:
        print(f"Updating existing assistant {existing_id} ...")
        resp = requests.patch(
            f"{VAPI_BASE_URL}/assistant/{existing_id}", headers=_headers(), json=payload
        )
        if resp.status_code == 404:
            # The stored ID no longer exists on Vapi's side (e.g. deleted in
            # dashboard) — fall through to creating a fresh one instead.
            existing_id = None
        elif not resp.ok:
            _fail("update assistant", resp)
        else:
            print(f"Assistant {existing_id} updated successfully.")
            return existing_id

    print("Creating new Vapi assistant ...")
    resp = requests.post(f"{VAPI_BASE_URL}/assistant", headers=_headers(), json=payload)
    if not resp.ok:
        _fail("create assistant", resp)

    assistant = resp.json()
    assistant_id = assistant["id"]
    with open(ASSISTANT_ID_FILE, "w") as f:
        f.write(assistant_id)

    print(f"Assistant created. ID: {assistant_id}")
    return assistant_id


def find_existing_phone_number_id() -> Optional[str]:
    """Look up whether TWILIO_PHONE_NUMBER has already been imported into Vapi."""
    resp = requests.get(f"{VAPI_BASE_URL}/phone-number", headers=_headers())
    if not resp.ok:
        _fail("list phone numbers", resp)

    for entry in resp.json():
        if entry.get("number") == TWILIO_PHONE_NUMBER:
            return entry["id"]
    return None


def connect_twilio_number(assistant_id: str):
    """
    Step 2: import the Twilio number into Vapi and point it at our assistant.

    Vapi's /phone-number endpoint uses the Twilio Account SID + Auth Token to
    reconfigure that number's voice webhook on Twilio's side automatically —
    there is no manual webhook URL to paste into the Twilio console.
    """
    payload = {
        "provider": "twilio",
        "number": TWILIO_PHONE_NUMBER,
        "twilioAccountSid": TWILIO_ACCOUNT_SID,
        "twilioAuthToken": TWILIO_AUTH_TOKEN,
        "assistantId": assistant_id,
    }

    existing_id = None
    if os.path.exists(PHONE_NUMBER_ID_FILE):
        with open(PHONE_NUMBER_ID_FILE, "r") as f:
            existing_id = f.read().strip() or None

    if not existing_id:
        existing_id = find_existing_phone_number_id()

    if existing_id:
        print(f"Updating existing Vapi phone number {existing_id} to point at assistant {assistant_id} ...")
        resp = requests.patch(
            f"{VAPI_BASE_URL}/phone-number/{existing_id}",
            headers=_headers(),
            json={"assistantId": assistant_id},
        )
        if not resp.ok:
            _fail("update phone number", resp)
        print(f"Phone number {TWILIO_PHONE_NUMBER} now routes to assistant {assistant_id}.")
        with open(PHONE_NUMBER_ID_FILE, "w") as f:
            f.write(existing_id)
        return

    print(f"Importing Twilio number {TWILIO_PHONE_NUMBER} into Vapi ...")
    resp = requests.post(f"{VAPI_BASE_URL}/phone-number", headers=_headers(), json=payload)
    if not resp.ok:
        _fail("import phone number", resp)

    phone_number = resp.json()
    phone_number_id = phone_number["id"]
    with open(PHONE_NUMBER_ID_FILE, "w") as f:
        f.write(phone_number_id)

    print(f"Twilio number imported and linked. Phone number ID: {phone_number_id}")


def main():
    args = parse_args()
    check_env_vars(args.webhook_url)
    # Show the URL up front: this is what gets baked into the assistant's tool
    # definition, and pushing a stale one silently breaks order delivery.
    print(f"Using webhook base URL: {args.webhook_url}")
    assistant_id = create_or_update_assistant(args.webhook_url)
    connect_twilio_number(assistant_id)
    print("\nSetup complete. Call your Twilio number to test the ordering flow.")


if __name__ == "__main__":
    main()
