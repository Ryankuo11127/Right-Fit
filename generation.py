import os
import uuid
import json
import base64
from pathlib import Path

import requests
import fal_client
from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

from config import OPENAI_API_KEY, FAL_KEY
from database import get_or_create_user, update_user_credits
import stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

router = APIRouter()

FAL_MODEL = "fal-ai/fashn/tryon/v1.6"

GENERATED_DIR = Path("generated")
TEMP_DIR = Path("temp_uploads")

GENERATED_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# production credits
BYPASS_CREDITS = False

if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY


def clear_folder(folder: Path):
    for item in folder.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)


def save_bytes(path: Path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def parse_json(text: str):
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    start_obj = text.find("{")
    end_obj = text.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        try:
            return json.loads(text[start_obj:end_obj + 1])
        except Exception:
            pass

    start_arr = text.find("[")
    end_arr = text.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        try:
            return json.loads(text[start_arr:end_arr + 1])
        except Exception:
            pass

    raise ValueError("Could not parse JSON from model output")


def detect_user_clothing(image_bytes: bytes) -> dict:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_data_url = f"data:image/png;base64,{image_b64}"

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": """
Look at this uploaded full-body photo.

Return ONLY valid JSON in this exact format:
{
  "sleeve": "short_sleeve" or "long_sleeve",
  "bottom": "shorts" or "long_pants",
  "presentation": "menswear" or "womenswear"
}

Rules:
- Only determine sleeve type, bottom type, and presentation
- Do NOT describe the outfit
- "sleeve" must be either "short_sleeve" or "long_sleeve"
- "bottom" must be either "shorts" or "long_pants"
- "presentation" must be either "menswear" or "womenswear"
- no explanation
- JSON only
"""
                    },
                    {
                        "type": "input_image",
                        "image_url": image_data_url
                    }
                ]
            }
        ]
    )

    clothing = parse_json(response.output_text)

    if clothing.get("sleeve") not in {"short_sleeve", "long_sleeve"}:
        raise ValueError("Invalid sleeve detection")
    if clothing.get("bottom") not in {"shorts", "long_pants"}:
        raise ValueError("Invalid bottom detection")
    if clothing.get("presentation") not in {"menswear", "womenswear"}:
        raise ValueError("Invalid presentation detection")

    return clothing


def validate_brand(brand: str) -> str | None:
    brand = (brand or "").strip()
    if not brand:
        return None

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=f"""
Determine whether this input is a real clothing/fashion brand that should be used for outfit styling.

Input: {brand}

Return ONLY valid JSON in this exact format:
{{
  "use_brand": true or false,
  "brand_name": "canonical brand name or empty string"
}}

Rules:
- use_brand = true only if this is a real clothing/fashion brand or widely recognized fashion label
- use_brand = false if it is gibberish, random words, unrelated, or not a clothing/fashion brand
- If use_brand is false, brand_name must be an empty string
- JSON only
"""
    )

    result = parse_json(response.output_text)

    if result.get("use_brand") is True:
        brand_name = str(result.get("brand_name", "")).strip()
        if brand_name:
            return brand_name

    return None


def classify_occasion(occasion: str, people: str) -> str:
    combined = f"{occasion} {people}".strip().lower()

    professional_keywords = [
        "job interview", "interview", "job meeting", "meeting", "office",
        "work", "business", "networking", "corporate", "conference",
        "professional", "internship", "career fair", "employer", "boss"
    ]
    formal_keywords = [
        "formal", "formal event", "wedding", "gala", "banquet", "ceremony", "black tie"
    ]
    social_keywords = [
        "party", "club", "rave", "night out"
    ]
    date_keywords = [
        "date", "dinner date", "first date", "dinner", "romantic", "crush",
        "girlfriend", "boyfriend", "partner"
    ]
    active_keywords = [
        "active", "outdoor", "hiking", "festival", "park", "amusement park", "workout", "gym"
    ]

    if any(k in combined for k in professional_keywords):
        return "professional"
    if any(k in combined for k in formal_keywords):
        return "formal"
    if any(k in combined for k in social_keywords):
        return "social_party"
    if any(k in combined for k in date_keywords):
        return "date"
    if any(k in combined for k in active_keywords):
        return "active_outdoor"

    return "casual"


def build_rules(clothing: dict, occasion: str, people: str) -> dict:
    category = classify_occasion(occasion, people)

    if clothing["sleeve"] == "long_sleeve":
        allowed_sleeves = ["long_sleeve"]
    else:
        allowed_sleeves = ["short_sleeve", "long_sleeve"]

    if clothing["bottom"] == "long_pants":
        allowed_bottoms = ["long_pants"]
    else:
        allowed_bottoms = ["shorts", "long_pants"]

    if category in {"professional", "formal"}:
        allowed_sleeves = ["long_sleeve"]
        allowed_bottoms = ["long_pants"]

    return {
        "occasion_category": category,
        "allowed_sleeves": allowed_sleeves,
        "allowed_bottoms": allowed_bottoms,
        "presentation": clothing["presentation"]
    }


def plan_three_outfits(
    age: str,
    occasion: str,
    people: str,
    clothing: dict,
    rules: dict,
    brand: str | None = None
) -> list:
    allowed_sleeves = ", ".join(rules["allowed_sleeves"])
    allowed_bottoms = ", ".join(rules["allowed_bottoms"])
    presentation = rules["presentation"]
    occasion_category = rules["occasion_category"]

    if brand:
        brand_instruction = f"""
Brand instruction:
- Use {brand} in each outfit in a realistic way.
- At least one prominent clothing piece in each outfit should reflect {brand}.
- This can be on the upper body or lower body depending on the outfit.
- Do NOT break the sleeve or bottom rules just to force the brand.
- Keep the outfit appropriate for the occasion even when using {brand}.
"""
    else:
        brand_instruction = """
Brand instruction:
- No brand preference. Ignore brand.
"""

    planner_prompt = f"""
You are a fashion stylist. Create EXACTLY 3 outfit plans as JSON.
They must match the user's scenario and obey the clothing rules.

User:
- age: {age}
- occasion: {occasion}
- people to impress: {people}

Detected from photo:
- sleeve: {clothing["sleeve"]}
- bottom: {clothing["bottom"]}
- presentation: {presentation}

Occasion category:
- {occasion_category}

Allowed sleeves:
- {allowed_sleeves}

Allowed bottoms:
- {allowed_bottoms}

{brand_instruction}

Critical rules:
1. All 3 outfits must fit the occasion and the people to impress.
2. All 3 outfits must be clearly different.
3. Do NOT reuse the same top, bottom, and shoe combination.
4. If allowed sleeves only contains long_sleeve, every outfit must be long_sleeve.
5. If allowed bottoms only contains long_pants, every outfit must be long_pants.
6. For professional or formal occasions, every outfit must be polished and appropriate.
7. Generate 3 NEW full outfits from top to bottom.
8. Each outfit must include a full top, full bottom, and shoes.
9. The uploaded photo is only used to determine the short/long clothing rules and presentation.

Return ONLY valid JSON as an array of 3 objects with this exact structure:
[
  {{
    "name": "short outfit name",
    "top_sleeve": "short_sleeve or long_sleeve",
    "bottom_type": "shorts or long_pants",
    "top_description": "specific top",
    "bottom_description": "specific bottom",
    "shoe_description": "specific shoes",
    "style_direction": "short style direction",
    "image_prompt": "one complete final image prompt for a full-body adult fashion model wearing this exact outfit in {presentation}"
  }}
]

Important:
- "image_prompt" must already be the FINAL prompt that should be sent directly to the OpenAI image generator
- Do NOT leave out the bottom
- Do NOT leave out the shoes
- The image_prompt must clearly describe a full-body model wearing the exact top, exact bottom, and exact shoes
- The image_prompt must mention full body, complete outfit visible, neutral studio background, and fashion photography
- The image_prompt must match the top_description, bottom_description, shoe_description, and style_direction

JSON only.
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=planner_prompt
    )

    outfits = parse_json(response.output_text)

    if not isinstance(outfits, list) or len(outfits) != 3:
        raise ValueError("Planner did not return exactly 3 outfits")

    seen_signatures = set()

    for outfit in outfits:
        if outfit.get("top_sleeve") not in rules["allowed_sleeves"]:
            raise ValueError("Planner returned invalid sleeve type")

        if outfit.get("bottom_type") not in rules["allowed_bottoms"]:
            raise ValueError("Planner returned invalid bottom type")

        top_description = str(outfit.get("top_description", "")).strip()
        bottom_description = str(outfit.get("bottom_description", "")).strip()
        shoe_description = str(outfit.get("shoe_description", "")).strip()
        image_prompt = str(outfit.get("image_prompt", "")).strip()

        if not top_description or not bottom_description or not shoe_description:
            raise ValueError("Planner returned incomplete outfit")

        if not image_prompt:
            raise ValueError("Planner returned missing image_prompt")

        signature = (
            top_description.lower(),
            bottom_description.lower(),
            shoe_description.lower()
        )

        if signature in seen_signatures:
            raise ValueError("Planner returned duplicate outfits")

        seen_signatures.add(signature)

    return outfits


def generate_outfit_reference_image(outfit: dict) -> bytes:
    prompt = str(outfit.get("image_prompt", "")).strip()
    if not prompt:
        raise ValueError("Missing image_prompt for image generation")

    result = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1024",
        n=1
    )

    return base64.b64decode(result.data[0].b64_json)


def get_user_and_credits(user_id: str):
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("Missing user_id")

    user = get_or_create_user(user_id)
  
    return user_id, user


@router.post("/generate-outfit-models")
async def generate_models(
    user_id: str = Form(...),
    age: str = Form(...),
    occasion: str = Form(...),
    people_to_impress: str = Form(...),
    brand: str = Form(""),
    photo: UploadFile = File(...)
):
    try:
        clear_folder(GENERATED_DIR)
        clear_folder(TEMP_DIR)

        user_bytes = await photo.read()

        user_id, user = get_user_and_credits(user_id)

        if not BYPASS_CREDITS and user["credits"] <= 0:
            return JSONResponse(
                status_code=403,
                content={
                    "message": "No credits left"
                }
            )

        clothing = detect_user_clothing(user_bytes)
        rules = build_rules(clothing, occasion, people_to_impress)
        validated_brand = validate_brand(brand)

        outfits = plan_three_outfits(
            age=age,
            occasion=occasion,
            people=people_to_impress,
            clothing=clothing,
            rules=rules,
            brand=validated_brand
        )

        user_file = TEMP_DIR / f"user_{uuid.uuid4()}.png"
        save_bytes(user_file, user_bytes)
        user_image_url = fal_client.upload_file(str(user_file))

        results = []

        for index, outfit in enumerate(outfits, start=1):
            outfit_bytes = generate_outfit_reference_image(outfit)
            reference_file = GENERATED_DIR / f"reference_{index}.png"
            save_bytes(reference_file, outfit_bytes)
            outfit_file = TEMP_DIR / f"outfit_{index}_{uuid.uuid4()}.png"
            save_bytes(outfit_file, outfit_bytes)
            garment_url = fal_client.upload_file(str(outfit_file))

            fal_result = fal_client.subscribe(
                FAL_MODEL,
                arguments={
                    "model_image": user_image_url,
                    "garment_image": garment_url,
                    "category": "one-pieces"
                }
            )

            image_url = fal_result["images"][0]["url"]

            r = requests.get(image_url, timeout=60)
            r.raise_for_status()

            saved_file = GENERATED_DIR / f"result_{index}.png"
            save_bytes(saved_file, r.content)

            results.append({
                "index": index,
                "saved_file": str(saved_file),
                "image_url": image_url,
                "outfit_name": outfit.get("name"),
                "top_description": outfit.get("top_description"),
                "bottom_description": outfit.get("bottom_description"),
                "shoe_description": outfit.get("shoe_description"),
                "image_prompt": outfit.get("image_prompt"),
            })

        clear_folder(TEMP_DIR)

        if not BYPASS_CREDITS:
            update_user_credits(user_id, user["credits"] - 1)

        return JSONResponse({
            "message": "3 outfits generated",
            "credits_bypassed": BYPASS_CREDITS,
            "detected_clothing": clothing,
            "occasion_category": rules["occasion_category"],
            "brand_requested": brand,
            "brand_applied": validated_brand,
            "results": results
        })

    except Exception as e:
        clear_folder(TEMP_DIR)
        return JSONResponse(
            status_code=500,
            content={
                "message": "Generation failed",
                "error": str(e)
            }
        )
@router.post("/create-checkout-session")
def create_checkout_session(user_id: str = Form(...)):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[{
                "price": "price_1TCOL5RPpZjFV74z9wkOl9Uk",
                "quantity": 1,
            }],
            success_url="https://deft-style-smart-fit.base44.app?user_id=" + user_id,
            cancel_url="https://deft-style-smart-fit.base44.app",
            metadata={
                "user_id": user_id
            }
        )

        return {"url": session.url}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        user_id = session["metadata"]["user_id"]

        user = get_or_create_user(user_id)
        update_user_credits(user_id, user["credits"] + 1)

        print(f"Added 1 credit to {user_id}")

    return {"received": True}
