from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import hashlib

from app.core.config import settings

router = APIRouter()


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def login(payload: LoginIn):
    if not settings.APP_PASSWORD:
        raise HTTPException(status_code=503, detail="未配置 APP_PASSWORD，登录不可用")
    if payload.password != settings.APP_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")
    token = hashlib.sha256(f"mr-auth:{settings.APP_PASSWORD}".encode()).hexdigest()
    return {"token": token}
