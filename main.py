from fastapi import FastAPI
from generation import router as generation_router
from database import get_or_create_user

app = FastAPI()

app.include_router(generation_router)


@app.get("/")
def home():
    return {"message": "RightFit backend running"}


@app.get("/credits/{user_id}")
def get_credits(user_id: str):
    user = get_or_create_user(user_id)
    return {
        "user_id": user_id,
        "credits": user["credits"]
    }