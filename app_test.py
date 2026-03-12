"""
MT5 Verify + Equity Conversion Service — Demo/Test Mode
- All MT5Manager dependencies removed
- Test account: login="test", password="master123"
- Real PAXG/XAUUSD price fetched from Binance
- Swap back the verify/redeem functions when deploying with real MT5Manager
"""

import os
import logging
from typing import Optional, Any, Dict
import json
import asyncio
import httpx
import secrets
import time
import random
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from dotenv import load_dotenv
from tg_notify import send_message

load_dotenv()

# ---------------------- Configuration & Logging ----------------------
LOG_FILE = os.getenv("LOG_FILE", "server.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BINANCE_BASE = os.getenv("BINANCE_BASE", "https://api.binance.com")
PAXG_SYMBOL = os.getenv("PAXG_SYMBOL", "PAXGUSDT")
PREMIUM_PERCENTAGE = float(os.getenv("PREMIUM_PERCENTAGE", "0.05"))

logging.basicConfig(
    filename=LOG_FILE,
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("mt5_service")

app = FastAPI(title="MT5 Verify + Equity Conversion Service")
templates = Jinja2Templates(directory="templates")


# ---------------------- Pydantic Models ----------------------
class VerifyRequest(BaseModel):
    mt5_login: str
    mt5_master_password: str


class VerifyResult(BaseModel):
    verified: bool
    reason: Optional[str] = None
    equity: Optional[float] = None
    positions: Optional[list] = None
    total_lot: Optional[float] = None
    paxg_price: Optional[float] = None
    redeemable_usdt: Optional[float] = None


# ---------------------- Utilities ----------------------
def mask_secret(value: Optional[str], show: int = 2) -> str:
    if not value:
        return ""
    if len(value) <= show * 2:
        return "*" * len(value)
    return value[:show] + "*" * (len(value) - (show * 2)) + value[-show:]


# ---------------------- Mock Verification (test account only) ----------------------
def verify_mt5_cred_blocking(login: str, master_password: str) -> Dict[str, Any]:
    """
    Demo mode: only the test account is accepted.
    Replace this entire function body with real MT5Manager calls for production.
    """
    if login == "test" and master_password == "master123":
        logger.info("Mock verification succeeded for login %s", login)
        equity = round(20000.0 + (10000.0 * random.random()), 2)
        return {
            "verified": True,
            "equity": equity,
            "positions": [
                {"ID": "1", "time": "2026-01-01 12:53:00", "symbol": "XAUUSD",   "volume": "0.01", "price": 5010.54},
                {"ID": "2", "time": "2026-01-01 13:19:00", "symbol": "XAUUSD",   "volume": "0.01", "price": 5005.10},
                {"ID": "3", "time": "2026-01-02 09:05:00", "symbol": "XAUUSD.s", "volume": "0.01", "price": 4998.75},
            ],
            "total_lot": 0.03,
        }

    logger.warning("Failed login attempt for login %s", login)
    return {"verified": False, "reason": "invalid credentials"}


# ---------------------- Binance price retrieval ----------------------
async def get_paxg_price() -> float:
    url = f"{BINANCE_BASE}/api/v3/ticker/price"
    params = {"symbol": PAXG_SYMBOL}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            price = float(r.json().get("price", 0.0))
            logger.info("Fetched PAXG price: %.4f", price)
            return price
    except Exception:
        logger.exception("Failed to fetch PAXG price from Binance; using fallback")
        return 5150.50


# ---------------------- Routes ----------------------
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return "<html><body><h3>MT5 Verify Service — up (demo mode)</h3></body></html>"


@app.get("/verify", response_class=HTMLResponse)
async def verify_form(request: Request, redirect: Optional[str] = None):
    return templates.TemplateResponse("verify.html", {"request": request, "redirect": redirect or ""})


@app.post("/api/verify", response_model=VerifyResult)
async def api_verify(mt5_login: str = Form(...), mt5_master_password: str = Form(...)):
    if not mt5_login or not mt5_master_password:
        raise HTTPException(status_code=400, detail="mt5_login and mt5_master_password are required")

    logger.info("Verification request for login %s", mt5_login)

    try:
        verification = await asyncio.to_thread(verify_mt5_cred_blocking, mt5_login, mt5_master_password)
    except Exception as exc:
        logger.exception("Verification raised an exception")
        return VerifyResult(verified=False, reason=str(exc))

    if not verification.get("verified"):
        return VerifyResult(verified=False, reason=verification.get("reason", "invalid credentials"))

    equity = float(verification.get("equity", 0.0))
    positions = verification.get("positions", [])
    total_lot = float(verification.get("total_lot", 0.0))
    logger.info("Verified login %s | equity=%.2f total_lot=%.4f", mt5_login, equity, total_lot)

    try:
        paxg_price = await get_paxg_price()
    except Exception as exc:
        logger.exception("Failed fetching PAXG price")
        return VerifyResult(verified=False, reason="failed to fetch paxg price")

    redeemable = equity * (1 - PREMIUM_PERCENTAGE) / paxg_price if paxg_price > 0 else 0.0

    return VerifyResult(
        verified=True,
        equity=equity,
        positions=positions,
        paxg_price=paxg_price,
        total_lot=total_lot,
        redeemable_usdt=redeemable,
    )


@app.post("/api/redeem")
async def api_redeem(
    mt5_login: str = Form(...),
    mt5_master_password: str = Form(...),
    redeem_grams: float = Form(...),
    premium: float = Form(1.05),
    position_details: list = Form(...),
):

    logger.info("Redeem request: login=%s grams=%.2f", mt5_login, redeem_grams)

    # --- Parse position_details (may arrive as a JSON string inside a list) ---
    try:
        if isinstance(position_details, str):
            parsed_positions = json.loads(position_details)
        elif isinstance(position_details, list) and len(position_details) == 1 and isinstance(position_details[0], str):
            parsed_positions = json.loads(position_details[0])
        else:
            parsed_positions = position_details or []
    except Exception as exc:
        logger.exception("Failed to parse position_details")
        raise HTTPException(status_code=400, detail="Invalid position_details format; expected JSON array")

    if not isinstance(parsed_positions, list):
        raise HTTPException(status_code=400, detail="position_details must be a JSON array")

    # # 1) Verify credentials
    verification = await asyncio.to_thread(verify_mt5_cred_blocking, mt5_login, mt5_master_password)
    if not verification.get("verified"):
        raise HTTPException(status_code=403, detail="Invalid MT5 credentials")

    # 2) Validate grams
    if redeem_grams < 50 or (redeem_grams % 50) != 0:
        raise HTTPException(status_code=400, detail="Redeem grams must be >= 50 and a multiple of 50")

    # 3) Compute required equity
    paxg_price = await get_paxg_price()
    oz = redeem_grams / 31.1035
    required_equity = oz * paxg_price * float(premium)

    # 4) Check equity
    equity = float(verification.get("equity", 0))
    if equity < required_equity:
        raise HTTPException(status_code=400, detail="Insufficient equity for this redemption")

    logger.info("Redeem check passed: login=%s grams=%.2f required=%.2f available=%.2f",
                mt5_login, redeem_grams, required_equity, equity)

    # 5) Validate that submitted position IDs belong to this user
    user_positions = verification.get("positions", []) or []
    user_pos_ids = set()
    for p in user_positions:
        id_val = p.get("ID") or p.get("id") or p.get("position_id") or p.get("ticket")
        if id_val is not None:
            user_pos_ids.add(str(id_val))

    for pos in parsed_positions:
        incoming_id = str(pos.get("id") or pos.get("ID") or pos.get("position_id") or pos.get("ticket") or "")
        if incoming_id == "":
            raise HTTPException(status_code=400, detail=f"Position entry missing id: {pos}")
        if incoming_id not in user_pos_ids:
            raise HTTPException(status_code=400, detail=f"Position id {incoming_id} not owned by user")

    # 6) Mock redemption execution (replace with real MT5Manager calls in production)
    tx_id = f"TX{int(time.time())}{secrets.token_hex(3)}"
    logger.info("Mock redemption executed: login=%s tx_id=%s", mt5_login, tx_id)

    # Notify via Telegram if configured
    try:
        bot_token = os.getenv("BOT_TOKEN_TV")
        chat_id = os.getenv("CHAT_ID_TV")
        if bot_token and chat_id:
            send_message(bot_token, chat_id,
                         f"[DEMO] Redemption processed\nLogin: {mt5_login}\nGrams: {redeem_grams}\nTX ID: {tx_id}")
    except Exception:
        logger.exception("Telegram notification failed (non-fatal)")

    return {"success": True, "message": "Redemption processed (demo mode)", "tx_id": tx_id}


# ---------------------- Run ----------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
