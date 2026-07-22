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

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

app = Flask(__name__)

RESTAURANT_NAME = "Rainbow Restaurant / 彩虹餐厅"


def print_order_confirmation(args: dict):
    """Prints the finalized order in the exact format requested."""
    items = args.get("items", [])
    item_lines = []
    for item in items:
        name = item.get("name", "Unknown item")
        qty = item.get("quantity", 1)
        mods = item.get("modifications", "")
        line = f"{qty}x {name}"
        if mods:
            line += f" ({mods})"
        item_lines.append(line)

    items_str = "; ".join(item_lines) if item_lines else "(none)"

    print("\nORDER CONFIRMED")
    print("===============")
    print(f"Restaurant: {RESTAURANT_NAME}")
    print(f"Customer Name: {args.get('customer_name', 'Unknown')}")
    print(f"Customer Phone: {args.get('customer_phone', 'Unknown')}")
    print(f"Items: {items_str}")
    print(f"Total: ${args.get('total', 0)}")
    print("Pickup Time: ~10 minutes")
    print("===============\n")


def _extract_tool_calls(payload: dict):
    """
    Vapi's tool-call webhook body looks like:
      {"message": {"type": "tool-calls",
                   "toolCallList": [{"id": ..., "function": {"name": ..., "arguments": {...}}}]}}
    `arguments` may arrive as a dict or as a JSON string depending on the
    model/provider, so we normalize both cases.
    """
    import json

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


@app.route("/tool-calls", methods=["POST"])
def tool_calls():
    payload = request.get_json(force=True, silent=True) or {}
    calls = _extract_tool_calls(payload)

    results = []
    for call in calls:
        if call["name"] == "logOrder":
            print_order_confirmation(call["arguments"])
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

    if message.get("type") == "end-of-call-report":
        print("\n--- Call ended (end-of-call-report) ---")
        print(f"Ended reason: {message.get('endedReason', 'unknown')}")
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
