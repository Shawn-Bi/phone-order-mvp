# Rainbow Restaurant — AI Phone Ordering MVP

One feature only: a caller dials your Twilio number, an AI assistant (Vapi + OpenAI)
takes their order in English or Mandarin, and the finalized order gets printed to
your console.

## Files

- `assistant_config.py` — the system prompt, menu, and the `logOrder` function/tool schema
- `setup_vapi.py` — creates the Vapi assistant and connects your Twilio number to it
- `webhook_server.py` — local server that receives the order data mid-call and prints it
- `.env.example` — copy to `.env` and fill in your keys

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Fill in your `.env`

```bash
cp .env.example .env
```

Fill in `VAPI_PRIVATE_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_PHONE_NUMBER`, and `OPENAI_API_KEY`. Note: Vapi calls OpenAI on your
behalf using ITS OWN OpenAI integration — if you want Vapi to bill your own
OpenAI account instead of using Vapi's pooled credits, add your OpenAI key as
a "Provider Key" in the Vapi dashboard (Settings -> Provider Keys). This MVP
doesn't call the OpenAI API directly from Python.

### 3. Start the webhook server

```bash
python webhook_server.py
```

This listens on `http://localhost:5000` (change with `WEBHOOK_PORT`).

### 4. Expose it publicly (Vapi needs a real URL, not localhost)

In a second terminal:

```bash
ngrok http 5000
```

Copy the `https://...ngrok-free.app` URL it gives you into `.env` as
`WEBHOOK_BASE_URL`.

### 5. Create the assistant and connect Twilio

```bash
python setup_vapi.py
```

This will:
1. Create a Vapi assistant using the bilingual system prompt and the
   `logOrder` tool (pointed at `WEBHOOK_BASE_URL/tool-calls`)
2. Import your Twilio number into Vapi and link it to that assistant —
   Vapi uses your Twilio Account SID/Auth Token to configure the number's
   voice webhook automatically, so there's nothing to paste into the
   Twilio console yourself.

Re-running this script later updates the existing assistant/number instead
of creating duplicates (IDs are cached in `assistant_id.txt` /
`phone_number_id.txt`).

### 6. Call it

Dial your Twilio number. Talk to it in English or Mandarin. When the
assistant finishes taking your order and confirms your name/phone, watch
the `webhook_server.py` terminal — it prints:

```
ORDER CONFIRMED
===============
Restaurant: Rainbow Restaurant / 彩虹餐厅
Customer Name: ...
Customer Phone: ...
Items: ...
Total: $...
Pickup Time: ~10 minutes
===============
```

## How the order gets logged

The assistant is given a function tool called `logOrder` (defined in
`assistant_config.py`) with a strict schema (customer name, phone, line
items with modifications, total). The prompt instructs the model to call
this function once — after it has read the order back and collected the
customer's name/phone, but before saying goodbye. Vapi calls our
`/tool-calls` webhook with the arguments in real time, which is why the
console print happens *during* the call, not after.

As a backup, `webhook_server.py` also listens on `/call-events` for Vapi's
end-of-call report (transcript + summary), in case the model ever forgets
to call the function.

## Notes / things you may want to tune

- **Voice**: uses OpenAI's TTS voice (`alloy`) via Vapi's built-in OpenAI
  voice provider, since only an OpenAI key was provided. If call quality
  in Mandarin isn't good enough, swap the `voice` block in
  `assistant_config.py` for an Azure or ElevenLabs multilingual voice
  (requires adding that provider's key in the Vapi dashboard).
- **Transcription**: uses Deepgram `nova-2` with `language: "multi"` for
  English/Mandarin auto-detection. This uses Vapi's pooled Deepgram access
  by default — no separate Deepgram key required.
- **No database**: order data is only printed to the console, per the MVP
  scope. Nothing is persisted.
