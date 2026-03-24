from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from generation import router as generation_router
from stripe_routes import router as stripe_router
from database import get_or_create_user

app = FastAPI()

# CORS (allows frontend to call backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://deft-style-smart-fit.base44.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generation_router)
app.include_router(stripe_router)

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
