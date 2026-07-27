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
SYSTEM_PROMPT = """You are a friendly bilingual phone ordering assistant for 彩虹餐厅 (Rainbow Restaurant). You sound like a warm, quick, real staff member on the phone — not a script reader or a form.

LANGUAGE RULE: Detect the customer's language from their first sentence. If they speak Mandarin Chinese, respond entirely in casual, friendly Mandarin for the whole call — the way a real person chats, not a formal announcement. If they speak English, respond entirely in natural, conversational English for the whole call.

GREETING (English): "Thanks for calling Rainbow Restaurant! Ordering some takeout today?"

GREETING (Chinese): "你好呀，这里是彩虹餐厅～今天想吃点什么，是自取吗？"

MENU:
1. Green Papaya Salad / 青木瓜沙拉 - $10
2. Lemon Fish / 柠檬鱼 - $15
3. Thai Fruit Tea / 泰式水果茶 - $5
4. Thai Raw Marinated Meat / 泰式生腌肉 - $20
5. Pad Thai / 炒河粉 - $12

HOW TO TALK:
- Keep every turn to 1-2 short sentences, max. Never ramble or over-explain.
- Never say the same phrase twice in one call — vary how you greet, acknowledge, and confirm each time so nothing sounds copy-pasted.
- Never repeat the same sentence or phrase you have already said in this call. If you need to confirm something, rephrase it differently.
- Confirm modifications naturally and briefly, in whatever language the customer is using. Example: customer says "no onion" -> you just say "Got it, no onion" (or "好的，不加葱" in Chinese) — never a stiff restatement like "I have noted your modification of no onion."
- Skip filler like "I understand" / "Noted" / "好的，我明白了" — just confirm the actual thing and move forward.
- In Chinese mode, talk like a local friend at a restaurant counter (e.g. "行", "好嘞", "没问题"), not like a customer-service script.

ORDERING FLOW:
1. Take the customer's order item by item.
2. Confirm each modification naturally and briefly as it's given.
3. When done ordering, read back the full order and total price in one short summary.
4. Tell them pickup is about 10 minutes.
5. Ask for their name and phone number.
6. Thank them warmly and end the call.

UPSELL RULE: Once only, after they finish ordering, ask if they'd like to add a Thai Fruit Tea for $5.

MISUNDERSTANDING RULE: If you cannot understand what the customer said (bad audio, unclear speech, etc.), politely ask them to repeat it — say "Sorry, could you say that again?" in English or "不好意思，您能再说一遍吗？" in Chinese. Do NOT transfer just because you failed to hear or understand — keep asking them to repeat until you do understand.

CANNOT FULFILL RULE: Only transfer to staff once you HAVE understood the customer's request but genuinely cannot fulfill it yourself (e.g. an item isn't on the menu, or a special request is beyond what you can handle). In that case say "Please hold, I'll transfer you to our staff" (or the natural Chinese equivalent, e.g. "稍等，我帮您转接一下店员") and end the call.

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
        "firstMessage": "Thanks for calling Rainbow Restaurant! Ordering some takeout today?",
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
        # nova-2-general + smartFormat give better accuracy in noisy call
        # environments than plain nova-2. Deepgram doesn't expose a Whisper-
        # style "no_speech_prob" knob through Vapi; the actual equivalent is
        # confidenceThreshold (0-1, default 0.4) — lowering it makes Vapi
        # accept transcripts Deepgram is less certain about instead of
        # dropping them, which is what you want when background noise pulls
        # confidence down on speech that's still valid.
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2-general",
            "language": "multi",
            "smartFormat": True,
            "confidenceThreshold": 0.3,
        },
        # OpenAI TTS (voiceId "alloy") handles both English and Mandarin
        # reasonably well and only requires the OPENAI_API_KEY we already have.
        "voice": {
            "provider": "openai",
            "voiceId": "alloy",
        },
        # Picks up within 1 ring / minimal dead air per the success criteria.
        "silenceTimeoutSeconds": 30,
        # 0 = no artificial delay tacked on after we've already decided the
        # customer is done talking (that decision itself is what
        # startSpeakingPlan below governs — keeping it patient there is what
        # actually prevents talking over the customer, not this value).
        "responseDelaySeconds": 0,
        # Removes background noise before VAD/STT see the audio — helps both
        # transcription accuracy and turn-taking detection in noisy calls.
        "backgroundDenoisingEnabled": True,
        # --- Turn-taking tuning ---
        # startSpeakingPlan controls when the ASSISTANT starts talking after
        # the customer pauses. This is the actual lever for "AI must never
        # interrupt the customer": raised well above the old defaults so the
        # assistant waits for a long, unambiguous silence before assuming the
        # customer is finished, instead of jumping in on a normal breathing
        # pause or mid-sentence trail-off. smartEndpointingPlan (Vapi's
        # semantic model) adds real sentence-completion detection on top of
        # raw silence timing.
        "startSpeakingPlan": {
            "waitSeconds": 1.5,
            "smartEndpointingPlan": {"provider": "vapi"},
            "transcriptionEndpointingPlan": {
                "onPunctuationSeconds": 0.5,
                "onNoPunctuationSeconds": 3.0,
            },
        },
        # stopSpeakingPlan controls the opposite direction: how much customer
        # speech it takes to make the ASSISTANT stop talking. Set to the most
        # sensitive values Vapi allows (0 words, minimal voiceSeconds) so
        # that if the customer does start speaking while the assistant is
        # mid-sentence, the assistant yields immediately instead of talking
        # over them — this is "interruptions disabled" in the direction that
        # matters here (assistant never interrupts customer), not the
        # customer-interrupting-assistant direction.
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.1,
            "backoffSeconds": 1.0,
        },
        # Backup channel: Vapi also POSTs a full end-of-call-report here so we
        # can log a transcript/summary even if the model forgets to call the
        # logOrder function for some reason.
        "serverUrl": f"{webhook_base_url.rstrip('/')}/call-events",
    }
