from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_user_by_user_id(user_id: str):
    result = supabase.table("users").select("*").eq("user_id", user_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    return None


def create_user(user_id: str):
    result = supabase.table("users").insert({
        "user_id": user_id,
        "credits": 0
    }).execute()

    return result.data[0]


def get_or_create_user(user_id: str):
    user = get_user_by_user_id(user_id)

    if user:
        return user

    return create_user(user_id)
def update_user_credits(user_id: str, credits: int):
    result = supabase.table("users").update({
        "credits": credits
    }).eq("user_id", user_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    return None