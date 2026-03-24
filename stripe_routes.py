import json
from pathlib import Path
from fastapi import APIRouter, Request
import stripe
from config import STRIPE_WEBHOOK_SECRET
from database import get_or_create_user, update_user_credits

router = APIRouter()
PROCESSED_FILE = Path("processed_sessions.json")

if not PROCESSED_FILE.exists():
    PROCESSED_FILE.write_text("[]")

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        return {"error": "Invalid signature"}

    if event["type"] == "checkout.session.completed":

        session = event["data"]["object"]
        session_id = session["id"]

        processed = json.loads(PROCESSED_FILE.read_text())

        if session_id in processed:
            return {"message": "Already processed"}

        processed.append(session_id)
        PROCESSED_FILE.write_text(json.dumps(processed))

        if session.get("payment_status") == "paid":
            success_url = session.get("success_url", "")
            user_id = success_url.split("user_id=")[-1] if "user_id=" in success_url else None

            if user_id:
                user = get_or_create_user(user_id)
                update_user_credits(user_id, user["credits"] + 1)

    return {"received": True}
