"""
Builds the JSON payload that gets sent to Vapi's /assistant API.

This is kept in its own file (separate from setup_vapi.py) so the prompt,
menu, and tool schema are easy to find and edit without touching the
API-calling logic.
"""

# --- The system prompt exactly as specified, with one addition at the end:
# a TOOL USAGE instruction telling the model to call our `logOrder` function
# once the order is finalized. Without this, the model would just say the
# order out loud but we'd have no structured data to log on our server.
SYSTEM_PROMPT = """You are a friendly bilingual phone ordering assistant for 彩虹餐厅 (Rainbow Restaurant).

LANGUAGE RULE: Detect the customer's language from their first sentence. If they speak Mandarin Chinese, respond entirely in Mandarin for the whole call. If they speak English, respond entirely in English for the whole call.

GREETING (English): "Thank you for calling Rainbow Restaurant! I'm your AI ordering assistant. Are you placing a takeout order today?"

GREETING (Chinese): "感谢您致电彩虹餐厅！我是您的AI点餐助手，请问您今天是要点外卖自取吗？"

MENU:
1. Green Papaya Salad / 青木瓜沙拉 - $10
2. Lemon Fish / 柠檬鱼 - $15
3. Thai Fruit Tea / 泰式水果茶 - $5
4. Thai Raw Marinated Meat / 泰式生腌肉 - $20
5. Pad Thai / 炒河粉 - $12

ORDERING FLOW:
1. Take the customer's order item by item
2. Always confirm any modifications (e.g. spice level, no onion)
3. When done ordering, read back the complete order and total price
4. Tell them pickup is approximately 10 minutes
5. Ask for their name and phone number
6. Thank them and end the call

UPSELL RULE: Once only, after they finish ordering, ask if they'd like to add a Thai Fruit Tea for $5.

CANNOT HANDLE RULE: If you cannot understand the customer after 2 attempts, say "Please hold, I'll transfer you to our staff" and end the call.

TONE: Warm, efficient, natural. No robotic-sounding phrases.

TOOL USAGE: After you have read back the complete confirmed order and total, told the customer the pickup time, and collected their name and phone number, call the logOrder function with the finalized order details BEFORE you say your final goodbye. Call it exactly once, only when the order is fully confirmed."""

# --- Function/tool definition for logging the finalized order.
# `server.url` is where Vapi POSTs the arguments once the model decides to
# call this function during the live call. This must be a public URL
# (e.g. an ngrok tunnel pointing at webhook_server.py).
def build_log_order_tool(webhook_base_url: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "logOrder",
            "description": (
                "Call this exactly once, after the order has been fully read back to "
                "the customer, pickup time confirmed, and name/phone collected. "
                "Logs the finalized order for the restaurant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "Customer's name as given over the phone",
                    },
                    "customer_phone": {
                        "type": "string",
                        "description": "Customer's callback/pickup phone number",
                    },
                    "items": {
                        "type": "array",
                        "description": "Every item ordered, one entry per line item",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Menu item name"},
                                "quantity": {"type": "integer", "description": "Quantity ordered"},
                                "modifications": {
                                    "type": "string",
                                    "description": "Any modifications, e.g. 'no onion, mild spice'. Empty string if none.",
                                },
                                "price": {"type": "number", "description": "Line item price in USD"},
                            },
                            "required": ["name", "quantity", "price"],
                        },
                    },
                    "total": {"type": "number", "description": "Total order price in USD"},
                    "language": {
                        "type": "string",
                        "description": "Language the call was conducted in: 'en' or 'zh'",
                    },
                },
                "required": ["customer_name", "customer_phone", "items", "total"],
            },
        },
        "server": {"url": f"{webhook_base_url.rstrip('/')}/tool-calls"},
    }


def build_assistant_payload(webhook_base_url: str) -> dict:
    """Full request body for POST https://api.vapi.ai/assistant"""
    return {
        "name": "Rainbow Restaurant Ordering Assistant",
        "firstMessage": (
            "Thank you for calling Rainbow Restaurant! I'm your AI ordering assistant. "
            "Are you placing a takeout order today?"
        ),
        # Vapi will auto-detect and switch based on the model's own language
        # detection driven by the system prompt; firstMessage stays English
        # since we don't know the caller's language until they speak.
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0.7,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
            "tools": [build_log_order_tool(webhook_base_url)],
        },
        # Deepgram's "multi" language mode auto-detects/code-switches between
        # English and Mandarin per utterance, which is what the LANGUAGE RULE
        # in the prompt depends on.
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "multi",
        },
        # OpenAI TTS (voiceId "alloy") handles both English and Mandarin
        # reasonably well and only requires the OPENAI_API_KEY we already have.
        "voice": {
            "provider": "openai",
            "voiceId": "alloy",
        },
        # Picks up within 1 ring / minimal dead air per the success criteria.
        "silenceTimeoutSeconds": 30,
        "responseDelaySeconds": 0.3,
        # Backup channel: Vapi also POSTs a full end-of-call-report here so we
        # can log a transcript/summary even if the model forgets to call the
        # logOrder function for some reason.
        "serverUrl": f"{webhook_base_url.rstrip('/')}/call-events",
    }
