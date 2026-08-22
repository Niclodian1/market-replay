from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://marketuser:marketpass@postgres:5432/marketreplay"
    # True: 启动时 create_all+补列（旧行为）；False: schema 完全交给 alembic 管理
    DB_AUTO_DDL: bool = True
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]
    DEBUG: bool = False
    # 单用户登录密码；为空表示未配置，/api/auth/login 返回 503（不设弱默认值）
    APP_PASSWORD: str = ""
    DISCORD_WEBHOOK_URL: Optional[str] = None
    # 飞书自建应用凭证（用于上传图片换取 image_key）
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    ALERT_TIMEZONE: str = "Asia/Taipei"
    DAILY_REPORT_ENABLED: bool = True
    DAILY_REPORT_HOUR: int = 8
    DAILY_REPORT_MINUTE: int = 0
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    REPORT_EMAIL_FROM: Optional[str] = None
    REPORT_EMAIL_TO: Optional[str] = None
    # 缠论 (Chan theory) 信号接入: 实时告警 + 日报
    CHANLUN_ALERTS_ENABLED: bool = True
    CHANLUN_SCAN_INTERVAL_MIN: int = 60
    CHANLUN_SYMBOLS: str = "BTC-USD,000001.SS,399006.SZ,000688.SS,^IXIC,^GSPC,GC=F,SI=F,CL=F"
    CHANLUN_MIN_BARS: int = 120
    CHANLUN_LOOKBACK_HOURS: float = 72.0
    CHANLUN_REPORT_LOOKBACK_DAYS: int = 7
    # ── M3B 影子运行时（V2 影子决策/标注；默认关闭，全验收后才在本机 .env 开启）──
    # 独立 worker 读取已持久化的 4H AlertEvent，不改 monitor_engine；
    # 关闭时调度 job 零 DB/零网络。
    M3_SHADOW_ENABLED: bool = False
    # 首批 allowlist：仅 BINANCE:BTCUSDT（只证明 crypto UTC，混入股票/TradFi 会污染样本）
    M3_SHADOW_SYMBOLS: str = ""
    M3_SHADOW_TIMEFRAME: str = "4h"
    # 每次取各周期最多 K 线根数（猜测值，足够 M1 warmup；若实测不足记录并调此常量，
    # 不改 M1/M2/M3A 口径）
    M3_SHADOW_MAX_BARS: int = 700
    # 影子 job 调度间隔（分钟）
    M3_SHADOW_INTERVAL_MIN: int = 1
    # decision 失败退避（秒）
    M3_SHADOW_RETRY_DELAY_SECONDS: int = 300
    # 缺数据/部分成熟 backoff（秒；正常缺根不算 failed，但不得每分钟热循环）
    M3_SHADOW_LABEL_RETRY_DELAY_SECONDS: int = 900
    M3_SHADOW_MAX_ATTEMPTS: int = 6
    M3_SHADOW_INGEST_LIMIT: int = 200
    M3_SHADOW_LABEL_LIMIT: int = 200


settings = Settings()
