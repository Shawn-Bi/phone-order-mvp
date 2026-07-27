"""
Local webhook server that Vapi calls during/after a phone call.

Two routes:
  POST /tool-calls   -> fired mid-call when the assistant calls the
                        `logOrder` function (see assistant_config.py).
                        This is the primary, structured way we learn the
                        final order.
  POST /call-events  -> fired after the call ends with a full transcript/
                        summary (Vapi's "end-of-call-report"). Used only
                        as a backup so we still see *something* logged
                        even if the model never calls logOrder.

Must be reachable from the public internet (e.g. via `ngrok http 5000`)
before running setup_vapi.py, since that URL gets baked into the
assistant/tool configuration on Vapi's side.

Run:
    python webhook_server.py
"""

import json
import os
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

load_dotenv()

app = Flask(__name__)

RESTAURANT_NAME = "Rainbow Restaurant / 彩虹餐厅"
PICKUP_MINUTES = 10

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Structured, append-only order log (JSON Lines — one order per line).
# Contains customer names/phone numbers, so it's gitignored; never commit it.
ORDERS_LOG_FILE = "orders.jsonl"

_twilio_client = None


def _get_twilio_client() -> Client:
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client


def _normalize_phone(raw: str):
    """
    Best-effort conversion of whatever the customer said/typed into E.164,
    since Twilio requires that format. Assumes US/Canada (+1) when the
    customer gives a bare 10-digit number, which is the common case for
    this restaurant's callers.
    """
    if not raw:
        return None
    had_plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if had_plus:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def _format_items(items: list) -> list:
    lines = []
    for item in items:
        name = item.get("name", "Unknown item")
        qty = item.get("quantity", 1)
        mods = item.get("modifications", "")
        line = f"{qty}x {name}"
        if mods:
            line += f" ({mods})"
        lines.append(line)
    return lines


def send_order_sms(args: dict, pickup_dt: datetime, caller_number: str = None):
    """Texts the customer their order summary, total, and pickup time."""
    raw_phone = args.get("customer_phone", "")
    phone = _normalize_phone(raw_phone)

    if not phone and caller_number:
        # The model may mishear/mistranscribe a spoken phone number. Vapi
        # also gives us the actual telephony caller ID for the call
        # (already in E.164), so fall back to that instead of giving up.
        print(
            f"[SMS] customer_phone {raw_phone!r} from the model didn't normalize; "
            f"falling back to caller ID {caller_number!r}"
        )
        phone = _normalize_phone(caller_number)

    print(f"[SMS] Attempting to send to: {phone!r} (raw customer_phone was {raw_phone!r}, caller ID was {caller_number!r})")

    if not phone:
        print(f"[SMS] Skipped — could not parse a usable phone number from either source.")
        return

    items_str = ", ".join(_format_items(args.get("items", []))) or "(none)"
    language = args.get("language", "en")
    pickup_str = pickup_dt.strftime("%I:%M %p").lstrip("0")

    if language == "zh":
        body = (
            f"{RESTAURANT_NAME}\n"
            f"您的订单已确认：{items_str}\n"
            f"总计：${args.get('total', 0):.2f}\n"
            f"预计取餐时间：约{PICKUP_MINUTES}分钟后（{pickup_str}）"
        )
    else:
        body = (
            f"{RESTAURANT_NAME}\n"
            f"Order confirmed: {items_str}\n"
            f"Total: ${args.get('total', 0):.2f}\n"
            f"Pickup time: ~{PICKUP_MINUTES} min ({pickup_str})"
        )

    try:
        message = _get_twilio_client().messages.create(to=phone, from_=TWILIO_PHONE_NUMBER, body=body)
        print(f"[SMS] Sent order confirmation to {phone} (Twilio sid={message.sid}, status={message.status})")
    except TwilioException as e:
        print(f"[SMS] Twilio rejected the send to {phone}: {e}")
    except Exception as e:
        print(f"[SMS] Unexpected error sending to {phone}: {e}")


def log_order_record(args: dict, timestamp: datetime, pickup_dt: datetime):
    """Appends the finalized order to ORDERS_LOG_FILE as one structured JSON line."""
    record = {
        "timestamp": timestamp.isoformat(),
        "pickup_time": pickup_dt.isoformat(),
        "customer_name": args.get("customer_name", "Unknown"),
        "customer_phone": args.get("customer_phone", "Unknown"),
        "items": args.get("items", []),
        "total": args.get("total", 0),
        "language": args.get("language", "en"),
    }
    with open(ORDERS_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def print_order_confirmation(args: dict, timestamp: datetime, pickup_dt: datetime):
    """Prints the finalized order in the exact format requested."""
    items_str = "; ".join(_format_items(args.get("items", []))) or "(none)"

    print("\nORDER CONFIRMED")
    print("===============")
    print(f"Restaurant: {RESTAURANT_NAME}")
    print(f"Customer Name: {args.get('customer_name', 'Unknown')}")
    print(f"Customer Phone: {args.get('customer_phone', 'Unknown')}")
    print(f"Items: {items_str}")
    print(f"Total: ${args.get('total', 0):.2f}")
    print(f"Timestamp: {timestamp.isoformat(timespec='seconds')}")
    print(f"Pickup Time: {pickup_dt.strftime('%I:%M %p').lstrip('0')} (~{PICKUP_MINUTES} min)")
    print("===============\n")


def process_order(args: dict, caller_number: str = None):
    """Single entry point for a finalized order: log it, print it, text it."""
    timestamp = datetime.now()
    pickup_dt = timestamp + timedelta(minutes=PICKUP_MINUTES)

    log_order_record(args, timestamp, pickup_dt)
    print_order_confirmation(args, timestamp, pickup_dt)
    send_order_sms(args, pickup_dt, caller_number)


def _extract_tool_calls(payload: dict):
    """
    Vapi's tool-call webhook body looks like:
      {"message": {"type": "tool-calls",
                   "toolCallList": [{"id": ..., "function": {"name": ..., "arguments": {...}}}]}}
    `arguments` may arrive as a dict or as a JSON string depending on the
    model/provider, so we normalize both cases.
    """
    message = payload.get("message", {})
    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []

    parsed = []
    for call in tool_calls:
        function = call.get("function", {})
        name = function.get("name")
        raw_args = function.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {}
        parsed.append({"id": call.get("id"), "name": name, "arguments": raw_args})
    return parsed


def _extract_caller_number(payload: dict):
    """
    Vapi includes the actual telephony call metadata alongside `message`,
    e.g. message.call.customer.number — already in E.164 since it comes
    straight from Twilio, unlike whatever the model transcribed from speech.
    """
    message = payload.get("message", {})
    call = message.get("call") or payload.get("call") or {}
    customer = call.get("customer") or {}
    return customer.get("number")


@app.route("/tool-calls", methods=["POST"])
def tool_calls():
    payload = request.get_json(force=True, silent=True) or {}

    print("\n[DEBUG] /tool-calls raw payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    calls = _extract_tool_calls(payload)
    caller_number = _extract_caller_number(payload)
    print(f"[DEBUG] Caller ID from Vapi call metadata: {caller_number!r}")

    results = []
    for call in calls:
        print(f"[DEBUG] Tool call received: name={call['name']!r} arguments={call['arguments']!r}")
        if call["name"] == "logOrder":
            process_order(call["arguments"], caller_number)
            results.append(
                {"toolCallId": call["id"], "result": "Order logged successfully."}
            )
        else:
            results.append(
                {"toolCallId": call["id"], "result": f"Unknown tool: {call['name']}"}
            )

    # Vapi expects this exact shape as the response to a tool-calls webhook.
    return jsonify({"results": results})


@app.route("/call-events", methods=["POST"])
def call_events():
    payload = request.get_json(force=True, silent=True) or {}
    message = payload.get("message", {})

    print(f"\n[DEBUG] /call-events raw payload (type={message.get('type')!r}):")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if message.get("type") == "end-of-call-report":
        caller_number = _extract_caller_number(payload)
        print("\n--- Call ended (end-of-call-report) ---")
        print(f"Ended reason: {message.get('endedReason', 'unknown')}")
        print(f"Caller ID from call metadata: {caller_number!r}")
        summary = message.get("summary")
        if summary:
            print(f"Summary: {summary}")
        print("----------------------------------------\n")

    # Vapi doesn't require a particular body back for this event type.
    return jsonify({"received": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    print(f"Webhook server listening on http://0.0.0.0:{port}")
    print("Routes: POST /tool-calls, POST /call-events, GET /health")
    app.run(host="0.0.0.0", port=port)
