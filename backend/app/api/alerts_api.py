from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.models import Alert, User
from app.schemas.alerts import AlertCreate, AlertUpdate, AlertOut, AlertsResponse, VALID_CONDITIONS
from app.core.discord_webhook import send_discord_alert

router = APIRouter()

DEFAULT_USER_ID = 1


async def _ensure_default_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == DEFAULT_USER_ID))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(id=DEFAULT_USER_ID, username="default", password_hash="")
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


@router.get("", response_model=AlertsResponse)
async def list_alerts(db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    result = await db.execute(
        select(Alert)
        .where(Alert.user_id == DEFAULT_USER_ID)
        .order_by(Alert.created_at.desc())
    )
    alerts = result.scalars().all()
    return AlertsResponse(items=list(alerts), total=len(alerts))


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(body: AlertCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    if body.condition_type not in VALID_CONDITIONS:
        raise HTTPException(status_code=400, detail=f"不支持的条件类型: {body.condition_type}")
    alert = Alert(
        user_id=DEFAULT_USER_ID,
        name=body.name,
        symbol=body.symbol.upper(),
        condition_type=body.condition_type,
        threshold=body.threshold if body.threshold is not None else 0.0,
        timeframe=body.timeframe,
        discord_webhook_url=body.discord_webhook_url,
        ma_period=body.ma_period,
        repeating=body.repeating,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


@router.put("/{alert_id}", response_model=AlertOut)
async def update_alert(alert_id: int, body: AlertUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == DEFAULT_USER_ID)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    if body.name is not None:
        alert.name = body.name
    if body.threshold is not None:
        alert.threshold = body.threshold
    if body.timeframe is not None:
        alert.timeframe = body.timeframe
    if body.discord_webhook_url is not None:
        alert.discord_webhook_url = body.discord_webhook_url
    if body.is_active is not None:
        alert.is_active = body.is_active
        # 重新激活时清除触发状态
        if body.is_active:
            alert.is_triggered = False
            alert.triggered_at = None
    alert.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(alert)
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == DEFAULT_USER_ID)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    await db.delete(alert)
    await db.commit()


@router.post("/{alert_id}/test", response_model=dict)
async def test_alert_webhook(alert_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == DEFAULT_USER_ID)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    ok = await send_discord_alert(
        alert.discord_webhook_url,
        f"✅ 测试通知 — {alert.name}",
        f"**标的**: {alert.symbol}\n**条件**: {alert.condition_type}\n**阈值**: {alert.threshold}\n\n此消息为 Webhook 连通性测试，非真实告警。",
        color=0x3b82f6,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Discord Webhook 发送失败，请检查 URL")
    return {"success": True, "message": "测试消息已发送"}
