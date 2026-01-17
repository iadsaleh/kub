from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
import asyncio
import json
import os
import re
import queue
import secrets
import hashlib
import uuid
import sys
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from aiogram import Bot
import httpx
from httpx_socks import AsyncProxyTransport
from dotenv import load_dotenv

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.security import encrypt_token, decrypt_token, get_encryption_key

# Ensure env vars are loaded
load_dotenv()

# استيراد مكونات المشروع
from modules.database import (
    SessionLocal,
    User,
    Game,
    History,
    Task,
    GameTimeline,
    Coupon,
    SystemSetting,
    ChangeLog,
    WebUser,
    BotToken,
    BotAccess,
    ChatConversation,
    ChatMessage,
    Transaction,
    AuditLog,
    StoreCategory,
    StoreItem,
    StorePurchase,
)
from modules.s2s_engine import kun_engine

# Try to import backup utils
try:
    from modules.backup_utils import send_backup_to_admin, create_project_backup
except ImportError:
    send_backup_to_admin = None
    create_project_backup = None

app = FastAPI(title="KUN NEXUS Dashboard", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _mask_proxy_url(proxy_url: str) -> str:
    try:
        parts = httpx.URL(proxy_url)
        host = parts.host or ""
        port = f":{parts.port}" if parts.port else ""
        scheme = parts.scheme or "http"
        if parts.userinfo:
            return f"{scheme}://***:***@{host}{port}"
        return f"{scheme}://{host}{port}"
    except Exception:
        return "***"

def _tg_message_link(chat_id: int, message_id: int) -> str:
    try:
        cid = int(chat_id)
        mid = int(message_id)
    except Exception:
        return ""
    if cid >= 0:
        return ""
    s = str(cid)
    if s.startswith("-100"):
        internal = s.replace("-100", "", 1)
        return f"https://t.me/c/{internal}/{mid}"
    return ""

def _slugify(value: str) -> str:
    v = (value or "").strip().lower()
    out = []
    for ch in v:
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or secrets.token_hex(4)

def _ensure_default_store_categories(db: Session):
    if db.query(StoreCategory).count() > 0:
        return
    defaults = [
        ("سكربتات فريدا", "frida_scripts", 10),
        ("سكربتات غيم غارديان", "gameguardian_scripts", 20),
        ("دروس تهكير الألعاب باستخدام الحقن", "injection_tutorials", 30),
        ("دروس جمبرة", "jailbreak_tutorials", 40),
    ]
    for name, slug, order in defaults:
        db.add(StoreCategory(name=name, slug=slug, sort_order=order, is_active=True))
    db.commit()

async def _get_public_ip(proxy_url: str | None = None) -> str:
    client_kwargs = {"timeout": 10.0, "verify": False}
    transport = None
    if proxy_url:
        if proxy_url.startswith("socks"):
            transport = AsyncProxyTransport.from_url(proxy_url)
        else:
            client_kwargs["proxy"] = proxy_url
            
    async with httpx.AsyncClient(transport=transport, **client_kwargs) as client:
        urls = ["https://api.ipify.org?format=json"]
        if proxy_url and (proxy_url.startswith("http://") or proxy_url.startswith("https://")):
            urls = ["http://api.ipify.org?format=json", "https://api.ipify.org?format=json"]

        last_err: Exception | None = None
        for url in urls:
            try:
                r = await client.get(url)
                r.raise_for_status()
                try:
                    data = r.json()
                    ip = str((data or {}).get("ip") or "").strip()
                    if ip:
                        return ip
                except Exception:
                    ip = (r.text or "").strip()
                    if ip:
                        return ip
            except Exception as e:
                last_err = e
                continue
        raise last_err or RuntimeError("Failed to fetch public IP")

async def _get_proxy_source(url: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def _get_proxy_source_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text

# --- Pydantic Models ---
class BalanceRequest(BaseModel):
    amount: float

class BotTokenCreate(BaseModel):
    name: str
    token: str

class GlobalProxiesUpdate(BaseModel):
    proxies: list[str] = []

class ProxyImportRequest(BaseModel):
    source: str
    mode: str = "append"  # append | replace
    limit: int = 200

class StoreChannelsUpdate(BaseModel):
    channels: Optional[list[int]] = None
    channel_ids: Optional[list[int]] = None

class StoreCategoryCreate(BaseModel):
    name: str
    slug: str = ""
    sort_order: int = 0
    is_active: bool = True

class StoreCategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

class StoreItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None

class BotAccessCreate(BaseModel):
    user_id: int
    role: str
    name: str = ""

class BroadcastRequest(BaseModel):
    message: str

class CouponRequest(BaseModel):
    amount: float

class CouponCreate(BaseModel):
    code: Optional[str] = None
    amount: float

class WalletResetConfirm(BaseModel):
    password: str

class PriceRequest(BaseModel):
    price: float

class GameCreate(BaseModel):
    name: str
    package_name: str
    provider: str
    main_key: str
    advanced_data: str  # "default" or JSON string or "Lvl:Token" lines
    device_os: str = "android"
    price: float = 0.0

class GameUpdate(BaseModel):
    name: str
    alias: str # Added alias
    package_name: str
    provider: str
    json_configuration: str  # Raw JSON string
    is_active: bool = True
    price: float = 0.0

class PlatformSettings(BaseModel):
    adjust_server: bool = True
    adjust_client: bool = True
    appsflyer_server: bool = True
    appsflyer_client: bool = True
    singular_server: bool = True
    singular_client: bool = True

class OutboundProxyItem(BaseModel):
    base: str
    username: str = ""
    password: str = ""

class OutboundProxiesSettings(BaseModel):
    enabled: bool = False
    proxies: list[OutboundProxyItem] = []

class TimelineItem(BaseModel):
    step_name: str
    event_value: str
    day_offset: int
    hour_offset: int

class ResendRequest(BaseModel):
    new_body: str

class TimelineUpdate(BaseModel):
    steps: list[TimelineItem]

class LoginRequest(BaseModel):
    username: str
    password: str

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

class ChatMessageCreate(BaseModel):
    body: str

class ChatConversationCreate(BaseModel):
    kind: str = "user_admin"
    user_a_id: int
    user_b_id: Optional[int] = None

# --- Auth Helpers ---
SESSIONS = {} # token -> {user_id, role, username, expires}

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_current_user(request: Request):
    token = request.headers.get("Authorization")
    if not token:
        # For development ease, if no token, maybe return None or raise
        # But for strict RBAC, we should raise. 
        # However, to keep existing public pages working, we might make it optional 
        # and enforce in specific endpoints.
        return None
    
    token = token.replace("Bearer ", "")
    session = SESSIONS.get(token)
    if not session:
        return None
    
    if datetime.utcnow() > session["expires"]:
        del SESSIONS[token]
        return None
        
    return session

def require_admin(user = Depends(get_current_user)):
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def require_moderator(user = Depends(get_current_user)):
    if not user or user["role"] not in ["admin", "moderator"]:
        raise HTTPException(status_code=403, detail="Moderator access required")
    return user

# --- Helper for Bot ---
def get_bot_token():
    # Try getting specific token or list
    tokens = os.getenv("BOT_TOKENS", "")
    if tokens:
        return tokens.split(',')[0].strip()
    return os.getenv("BOT_TOKEN", "")

def get_bot_tokens():
    tokens = os.getenv("BOT_TOKENS", "")
    out = [t.strip() for t in (tokens or "").split(",") if t.strip()]
    if out:
        return out
    one = (os.getenv("BOT_TOKEN") or "").strip()
    return [one] if one else []

async def send_chat_message_to_telegram(user_id: int, text: str):
    token = get_bot_token()
    if not token:
        return
    bot = Bot(token=token)
    try:
        await bot.send_message(user_id, text)
    finally:
        await bot.session.close()

async def send_broadcast_message(message: str, db: Session):
    token = get_bot_token()
    if not token:
        return
    
    bot = Bot(token=token)
    try:
        users = db.query(User).all()
        for u in users:
            try:
                await bot.send_message(u.id, f"📢 **إشعار:**\n{message}")
                await asyncio.sleep(0.05)
            except:
                pass
    finally:
        await bot.session.close()

async def perform_manual_backup(admin_id: int):
    token = get_bot_token()
    if not token or not send_backup_to_admin:
        return
    
    bot = Bot(token=token)
    try:
        await send_backup_to_admin(bot, admin_id)
    finally:
        await bot.session.close()


# إعداد القوالب والملفات الثابتة
base_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(base_dir, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

# --- إدارة الـ WebSockets للسجلات ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()
# استخدام queue.Queue بدلاً من asyncio.Queue لضمان الأمان بين الخيوط (Threads)
log_queue = queue.Queue()

# دالة لحقن السجلات من النظام الرئيسي إلى الواجهة
async def log_injector(message: str):
    # يتم استدعاء هذه الدالة من الخيط الرئيسي (Main Thread)
    # نضع الرسالة في الطابور الآمن ليتم بثها من خيط خادم الويب
    log_queue.put(message)

# --- Financial Management Endpoints ---

@app.get("/api/admin/finance/stats")
def get_finance_stats(user: dict = Depends(require_admin)):
    db = SessionLocal()
    try:
        total_balance = db.query(func.sum(User.balance)).scalar() or 0.0
        
        # Transactions stats
        total_deposits = db.query(func.sum(Transaction.amount)).filter(Transaction.amount > 0).scalar() or 0.0
        total_withdrawals = db.query(func.sum(Transaction.amount)).filter(Transaction.amount < 0).scalar() or 0.0
        withdrawals_abs = abs(total_withdrawals or 0.0)

        withdrawals_offset_setting = db.query(SystemSetting).filter(SystemSetting.key == "withdrawals_offset").first()
        withdrawals_offset = 0.0
        if withdrawals_offset_setting is not None:
            try:
                withdrawals_offset = float(withdrawals_offset_setting.value)
            except Exception:
                withdrawals_offset = 0.0
        
        # Coupons stats
        total_coupons = db.query(func.count(Coupon.id)).scalar() or 0
        used_coupons = db.query(func.count(Coupon.id)).filter(Coupon.is_used == True).scalar() or 0

        # Spend by source (where money was spent)
        source_rows = (
            db.query(Transaction.source, func.sum(Transaction.amount))
            .filter(Transaction.amount < 0)
            .group_by(Transaction.source)
            .all()
        )
        spend_by_source = {}
        for src, total in source_rows:
            key = src or "UNKNOWN"
            spend_by_source[key] = abs(total or 0.0)
        
        return {
            "total_user_balance": total_balance,
            "total_deposits": total_deposits,
            "total_withdrawals": max(0.0, withdrawals_abs - withdrawals_offset),
            "coupons": {
                "total": total_coupons,
                "used": used_coupons,
                "active": total_coupons - used_coupons
            },
            "spend_by_source": spend_by_source
        }
    finally:
        db.close()

@app.get("/api/admin/transactions")
def list_transactions(
    user_id: Optional[int] = None,
    type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_admin)
):
    db = SessionLocal()
    try:
        query = db.query(Transaction)
        
        if user_id:
            query = query.filter(Transaction.user_id == user_id)
        if type:
            query = query.filter(Transaction.type == type)
            
        total = query.count()
        transactions = query.order_by(Transaction.created_at.desc()).offset(offset).limit(limit).all()
        
        return {
            "total": total,
            "data": [
                {
                    "id": t.id,
                    "user_id": t.user_id,
                    "amount": t.amount,
                    "type": t.type,
                    "source": t.source,
                    "description": t.description,
                    "created_at": t.created_at
                } for t in transactions
            ]
        }
    finally:
        db.close()

@app.get("/api/admin/coupons")
def list_coupons(user: dict = Depends(require_admin)):
    db = SessionLocal()
    try:
        coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()
        return [
            {
                "code": c.code,
                "amount": c.amount,
                "is_used": c.is_used,
                "used_by": c.used_by,
                "created_at": c.created_at
            } for c in coupons
        ]
    finally:
        db.close()

@app.post("/api/admin/coupons")
def create_coupon(req: CouponCreate, user: dict = Depends(require_admin)):
    db = SessionLocal()
    try:
        code = req.code
        if not code:
            code = "KUN-" + secrets.token_hex(4).upper()
            
        if db.query(Coupon).filter(Coupon.code == code).first():
            raise HTTPException(status_code=400, detail="Coupon code already exists")
            
        new_coupon = Coupon(
            code=code,
            amount=req.amount,
            is_used=False
        )
        db.add(new_coupon)
        
        # Audit Log
        audit = AuditLog(
            admin_id=user["user_id"],
            action="CREATE_COUPON",
            details={"code": code, "amount": req.amount}
        )
        db.add(audit)
        
        db.commit()
        return {"message": "Coupon created", "code": code}
    finally:
        db.close()

@app.delete("/api/admin/coupons/{code}")
def delete_coupon(code: str, user: dict = Depends(require_admin)):
    db = SessionLocal()
    try:
        coupon = db.query(Coupon).filter(Coupon.code == code).first()
        if not coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")
            
        db.delete(coupon)
        
        # Audit Log
        audit = AuditLog(
            admin_id=user["user_id"],
            action="DELETE_COUPON",
            details={"code": code}
        )
        db.add(audit)
        
        db.commit()
        return {"message": "Coupon deleted"}
    finally:
        db.close()

@app.post("/api/admin/finance/reset-wallets")
def reset_all_wallets(req: WalletResetConfirm, user: dict = Depends(require_admin)):
    """
    Secure wallet reset:
    1. Verify admin password.
    2. Create snapshot of current balances (AuditLog).
    3. Reset all balances to 0.
    4. Log transaction for each user (optional, but good for history).
    """
    db = SessionLocal()
    try:
        # 1. Verify Password
        admin_user = db.query(WebUser).filter(WebUser.id == user["user_id"]).first()
        if not admin_user or admin_user.password_hash != hash_password(req.password):
            raise HTTPException(status_code=403, detail="Invalid password")
            
        # 2. Create Snapshot
        users = db.query(User).filter(User.balance != 0).all()
        snapshot = {u.id: u.balance for u in users}
        
        if not snapshot:
            return {"message": "No users with non-zero balance found."}

        # Audit Log with Snapshot (for recovery)
        audit = AuditLog(
            admin_id=user["user_id"],
            action="RESET_WALLETS",
            details={"snapshot": snapshot, "count": len(users)}
        )
        db.add(audit)
        
        # 3. Reset Balances & 4. Log Transactions
        for u in users:
            old_balance = u.balance
            u.balance = 0.0
            
            # Record the reset as a transaction so user history is clean
            trans = Transaction(
                user_id=u.id,
                amount=-old_balance, # Deduct the full amount
                type="RESET",
                source="RESET_ALL",
                description=f"System Wallet Reset by Admin {user['username']}"
            )
            db.add(trans)
            
        db.commit()
        return {"message": f"Successfully reset {len(users)} wallets.", "snapshot_id": audit.id}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# مهمة خلفية لبث الرسائل من الطابور
async def broadcast_background_task():
    while True:
        try:
            while not log_queue.empty():
                message = log_queue.get_nowait()
                await manager.broadcast(message)
            await asyncio.sleep(0.1)
        except Exception:
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    print("[STARTUP] Starting web dashboard...")
    # Verify environment variables
    try:
        get_encryption_key()
        print("[STARTUP] Encryption key loaded")
    except ValueError as e:
        print(f"CRITICAL WARNING: {e}")
        # We don't exit to allow dev mode but logs will show issue
    
    # Sync .env to DB (Bot Tokens & Admin Access)
    db = SessionLocal()
    try:
        # 1. Sync Main Bot Token
        env_token = os.getenv("BOT_TOKEN")
        if env_token and ":" in env_token:
            # Check if exists (by comparing encrypted or just by name/existence logic)
            # Since we can't easily query encrypted, we'll check if we have ANY token named "Main Bot" or similar
            # Or better, we decrypt all and check.
            existing_tokens = db.query(BotToken).all()
            token_exists = False
            for t in existing_tokens:
                try:
                    if decrypt_token(t.token) == env_token:
                        token_exists = True
                        break
                except:
                    pass
            
            if not token_exists:
                print("Syncing BOT_TOKEN from .env to Database...")
                encrypted = encrypt_token(env_token)
                new_token = BotToken(name="Main Bot (.env)", token=encrypted)
                db.add(new_token)
                db.commit()

        # 2. Sync Admin ID
        env_admin = os.getenv("ADMIN_ID")
        if env_admin and env_admin.isdigit():
            admin_id = int(env_admin)
            if not db.query(BotAccess).filter(BotAccess.user_id == admin_id).first():
                print(f"Syncing ADMIN_ID {admin_id} from .env to Database...")
                new_access = BotAccess(user_id=admin_id, role="admin", name="Super Admin (.env)")
                db.add(new_access)
                db.commit()

    except Exception as e:
        print(f"Error syncing .env to DB: {e}")
    finally:
        db.close()

    asyncio.create_task(broadcast_background_task())
    
    # Initialize Default Admin
    db = SessionLocal()
    try:
        if not db.query(WebUser).filter(WebUser.username == "admin").first():
            admin = WebUser(username="admin", password_hash=hash_password("admin123"), role="admin")
            db.add(admin)
            db.commit()
            print("Created default admin user: admin / admin123")
    except Exception as e:
        print(f"Error initializing admin: {e}")
    finally:
        db.close()

# --- Dependency لقاعدة البيانات ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Endpoints ---

@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(WebUser).filter(WebUser.username == req.username).first()
    if not user or user.password_hash != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = secrets.token_hex(32)
    SESSIONS[token] = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "expires": datetime.utcnow() + timedelta(hours=24)
    }
    
    user.last_login = datetime.utcnow()
    db.commit()
    
    return {"token": token, "user": {"user_id": user.id, "username": user.username, "role": user.role}}

@app.get("/api/auth/me")
def get_me(user = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@app.post("/api/auth/logout")
def logout(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token in SESSIONS:
        del SESSIONS[token]
    return {"status": "success"}

@app.post("/api/auth/password")
def change_password(req: PasswordChangeRequest, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    user_obj = db.query(WebUser).filter(WebUser.username == user["username"]).first()
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user_obj.password_hash != hash_password(req.current_password):
        raise HTTPException(status_code=400, detail="Incorrect current password")
    
    user_obj.password_hash = hash_password(req.new_password)
    db.commit()
    
    return {"status": "success"}

@app.get("/api/bot/tokens")
def get_bot_tokens(db: Session = Depends(get_db), user = Depends(require_admin)):
    tokens = db.query(BotToken).all()
    result = []
    for t in tokens:
        try:
            # Try to decrypt to display masked version
            decrypted = decrypt_token(t.token)
            masked = f"{decrypted[:5]}...{decrypted[-5:]}" if len(decrypted) > 10 else "***"
        except:
            # If decryption fails (maybe old unencrypted token), show as is or error
            masked = "INVALID/ENCRYPT_FAIL"
            if ":" in t.token and len(t.token) > 10: # Fallback for plain text
                 masked = f"{t.token[:5]}...{t.token[-5:]}"

        result.append({
            "id": t.id, 
            "name": t.name, 
            "token": masked, 
            "is_active": t.is_active
        })
    return result

@app.post("/api/bot/tokens")
async def add_bot_token(req: BotTokenCreate, db: Session = Depends(get_db), user = Depends(require_admin)):
    # Check for duplicate (checking encrypted is hard without deterministic encryption, 
    # but we can check if we have it in memory or just allow addition and rely on ID)
    # Actually, for unique constraint on token column, we need deterministic encryption or check before encrypting.
    # Fernet is not deterministic (random IV). 
    # So we can't easily check for duplicates by query unless we decrypt all or use a hash column for lookup.
    # For now, we'll skip unique check on encrypted value or rely on name uniqueness?
    # Let's check name uniqueness at least.
    if db.query(BotToken).filter(BotToken.name == req.name).first():
        raise HTTPException(status_code=400, detail="Token name already exists")

    # Simple validation for Telegram Token format (123:ABC)
    if ":" not in req.token or len(req.token) < 20:
         raise HTTPException(status_code=400, detail="Invalid token format")

    # Verify with Telegram API
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"https://api.telegram.org/bot{req.token}/getMe", timeout=10.0)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Telegram API Error: {resp.text}")
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=400, detail=f"Invalid Token: {data.get('description')}")
        except httpx.RequestError as e:
             raise HTTPException(status_code=400, detail=f"Connection Error: {str(e)}")

    # Encrypt
    encrypted_token = encrypt_token(req.token)

    new_token = BotToken(name=req.name, token=encrypted_token)
    db.add(new_token)
    
    # Log change
    log = ChangeLog(editor=user["username"], action="add_bot_token", details={"name": req.name})
    db.add(log)
    
    db.commit()
    return {"status": "success"}

@app.delete("/api/bot/tokens/{token_id}")
def delete_bot_token(token_id: int, db: Session = Depends(get_db), user = Depends(require_admin)):
    token = db.query(BotToken).filter(BotToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    
    db.delete(token)
    
    # Log change
    log = ChangeLog(editor=user["username"], action="delete_bot_token", details={"name": token.name})
    db.add(log)
    
    db.commit()
    return {"status": "success"}

@app.get("/api/bot/access")
def get_bot_access(db: Session = Depends(get_db), user = Depends(require_admin)):
    access_list = db.query(BotAccess).all()
    return [{"id": a.id, "user_id": a.user_id, "role": a.role, "name": a.name} for a in access_list]

@app.post("/api/bot/access")
def add_bot_access(req: BotAccessCreate, db: Session = Depends(get_db), user = Depends(require_admin)):
    if db.query(BotAccess).filter(BotAccess.user_id == req.user_id).first():
        raise HTTPException(status_code=400, detail="User ID already exists in access list")
    
    new_access = BotAccess(user_id=req.user_id, role=req.role, name=req.name)
    db.add(new_access)
    
    # Log change
    log = ChangeLog(editor=user["username"], action="add_bot_access", details={"user_id": req.user_id, "role": req.role})
    db.add(log)
    
    db.commit()
    return {"status": "success"}

@app.delete("/api/bot/access/{access_id}")
def delete_bot_access(access_id: int, db: Session = Depends(get_db), user = Depends(require_admin)):
    access = db.query(BotAccess).filter(BotAccess.id == access_id).first()
    if not access:
        raise HTTPException(status_code=404, detail="Access entry not found")
    
    db.delete(access)
    
    # Log change
    log = ChangeLog(editor=user["username"], action="delete_bot_access", details={"user_id": access.user_id})
    db.add(log)
    
    db.commit()
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return FileResponse(os.path.join(base_dir, "templates", "index.html"))

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    """جلب إحصائيات لوحة التحكم"""
    total_users = db.query(User).count()
    total_games = db.query(Game).count()
    active_games = db.query(Game).filter(Game.is_active == True).count()
    total_events = db.query(History).count()
    
    # إحصائيات آخر 24 ساعة
    last_24h = datetime.utcnow() - timedelta(hours=24)
    events_24h = db.query(History).filter(History.timestamp >= last_24h).count()
    
    # توزيع المنصات مع نسب النجاح
    platforms_stats = {}
    results = db.query(History.platform, History.status_code, func.count(History.id)).group_by(History.platform, History.status_code).all()
    
    # Aggregate results
    for platform, status, count in results:
        p_name = platform or "Unknown"
        if p_name not in platforms_stats:
            platforms_stats[p_name] = {"total": 0, "success": 0}
        
        platforms_stats[p_name]["total"] += count
        if 200 <= status < 300:
            platforms_stats[p_name]["success"] += count
            
    return {
        "users": total_users,
        "games": {"total": total_games, "active": active_games},
        "events": {"total": total_events, "last_24h": events_24h},
        "platforms": platforms_stats
    }

@app.get("/api/games")
def get_games(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    query = db.query(Game)
    
    if user and user["role"] == "admin":
        # Admin sees all games
        games = query.all()
    elif user:
        # Users see Global games + their own Private games
        games = query.filter((Game.owner_id == None) | (Game.owner_id == user["user_id"])).all()
    else:
        # Guests see only Global games
        games = query.filter(Game.owner_id == None).all()

    return [{
        "id": g.id,
        "name": g.name,
        "alias": g.alias,
        "package_name": g.package_name,
        "device_os": getattr(g, "device_os", None) or (g.json_data or {}).get("device_os") or "android",
        "provider": g.provider,
        "is_active": g.is_active,
        "owner_id": g.owner_id,
        "json_data": g.json_data 
    } for g in games]

@app.get("/api/game/{game_id}")
def get_game_details(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {
        "id": game.id,
        "name": game.name,
        "alias": game.alias,
        "package_name": game.package_name,
        "provider": game.provider,
        "json_data": game.json_data,
        "is_active": game.is_active
    }

@app.post("/api/game/toggle/{game_id}")
def toggle_game(game_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    game.is_active = not game.is_active
    db.commit()
    return {"status": "success", "new_state": game.is_active}

@app.get("/api/users")
def get_users(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    users = db.query(User).offset(skip).limit(limit).all()
    return [{
        "id": u.id,
        "balance": u.balance,
        "is_banned": u.is_banned,
        "gaid": u.profile_data.get("saved_gaid", "N/A"),
        "proxy_enabled": bool((u.profile_data or {}).get("proxy_base")),
        "proxy_base": (u.profile_data or {}).get("proxy_base", ""),
        "proxy_has_auth": bool((u.profile_data or {}).get("proxy_username")),
        "last_active": u.last_active.strftime("%Y-%m-%d %H:%M:%S") if u.last_active else "N/A"
    } for u in users]

@app.get("/api/chat/conversations")
def list_chat_conversations(db: Session = Depends(get_db), user: dict = Depends(require_moderator)):
    convs = db.query(ChatConversation).filter(ChatConversation.kind == "user_admin").order_by(ChatConversation.updated_at.desc()).limit(200).all()
    out = []
    for c in convs:
        last = db.query(ChatMessage).filter(ChatMessage.conversation_id == c.id).order_by(ChatMessage.created_at.desc()).first()
        out.append({
            "id": c.id,
            "kind": c.kind,
            "user_a_id": c.user_a_id,
            "user_b_id": c.user_b_id,
            "is_closed": c.is_closed,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "last_message": {
                "id": last.id,
                "sender_role": last.sender_role,
                "sender_user_id": last.sender_user_id,
                "body": last.body,
                "created_at": last.created_at.isoformat() if last.created_at else None
            } if last else None
        })
    return out

@app.post("/api/chat/conversations")
def create_chat_conversation(req: ChatConversationCreate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    kind = (req.kind or "user_admin").lower()
    user_a_id = int(req.user_a_id)
    user_b_id = int(req.user_b_id) if req.user_b_id is not None else None

    if kind != "user_admin":
        raise HTTPException(status_code=400, detail="Only user_admin conversations are allowed")

    existing = db.query(ChatConversation).filter(ChatConversation.kind == "user_admin", ChatConversation.user_a_id == user_a_id, ChatConversation.user_b_id == None, ChatConversation.is_closed == False).first()
    if existing:
        return {"id": existing.id}
    conv = ChatConversation(kind="user_admin", user_a_id=user_a_id, user_b_id=None)

    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"id": conv.id}

@app.get("/api/chat/conversations/{conversation_id}/messages")
def get_chat_messages(conversation_id: int, db: Session = Depends(get_db), user: dict = Depends(require_moderator)):
    msgs = db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.created_at.asc()).limit(500).all()
    return [{
        "id": m.id,
        "conversation_id": m.conversation_id,
        "sender_role": m.sender_role,
        "sender_user_id": m.sender_user_id,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None
    } for m in msgs]

@app.post("/api/chat/conversations/{conversation_id}/messages")
async def post_chat_message(conversation_id: int, req: ChatMessageCreate, db: Session = Depends(get_db), user: dict = Depends(require_moderator)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.is_closed:
        raise HTTPException(status_code=400, detail="Conversation is closed")
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Empty message")

    msg = ChatMessage(conversation_id=conversation_id, sender_role="admin", sender_user_id=None, body=body)
    db.add(msg)
    conv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(msg)

    if conv.kind == "user_admin":
        await send_chat_message_to_telegram(conv.user_a_id, f"💬 رسالة من الإدارة:\n{body}")

    return {"id": msg.id}

@app.get("/api/history")
def get_history(limit: int = 50, db: Session = Depends(get_db)):
    history = db.query(History).order_by(History.timestamp.desc()).limit(limit).all()
    return [{
        "id": h.id,
        "user_id": h.user_id,
        "game": h.game_alias,
        "event": h.event_name,
        "status": h.status_code,
        "time": h.timestamp.strftime("%H:%M:%S"),
        "response_time": h.response_time_ms
    } for h in history]

@app.get("/api/history/search")
def search_history(
    q: Optional[str] = None,
    user_id: Optional[int] = None,
    game: Optional[str] = None,
    platform: Optional[str] = None,
    event: Optional[str] = None,
    status_code: Optional[int] = None,
    status_min: Optional[int] = None,
    status_max: Optional[int] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    def parse_dt(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except Exception:
                return None

    query = db.query(History)

    if user_id is not None:
        query = query.filter(History.user_id == user_id)
    if game:
        query = query.filter(History.game_alias.ilike(f"%{game}%"))
    if platform:
        query = query.filter(History.platform.ilike(f"%{platform}%"))
    if event:
        query = query.filter(History.event_name.ilike(f"%{event}%"))

    if status_code is not None:
        query = query.filter(History.status_code == status_code)
    if status_min is not None:
        query = query.filter(History.status_code >= status_min)
    if status_max is not None:
        query = query.filter(History.status_code <= status_max)

    dt_from = parse_dt(from_ts) if from_ts else None
    dt_to = parse_dt(to_ts) if to_ts else None
    if dt_from:
        query = query.filter(History.timestamp >= dt_from)
    if dt_to:
        query = query.filter(History.timestamp <= dt_to)

    if q:
        like = f"%{q}%"
        query = query.filter((History.request_body.like(like)) | (History.response_text.like(like)))

    total = query.count()
    rows = (
        query.order_by(History.timestamp.desc())
        .offset(max(0, offset))
        .limit(min(max(1, limit), 200))
        .all()
    )

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": h.id,
                "user_id": h.user_id,
                "game": h.game_alias,
                "event": h.event_name,
                "platform": h.platform,
                "status": h.status_code,
                "time": h.timestamp.isoformat(),
                "response_time": h.response_time_ms,
            }
            for h in rows
        ],
    }

@app.get("/api/history/{history_id}")
def get_history_details(history_id: int, db: Session = Depends(get_db)):
    h = db.query(History).filter(History.id == history_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="History entry not found")
    
    return {
        "id": h.id,
        "user_id": h.user_id,
        "game": h.game_alias,
        "platform": h.platform,
        "event": h.event_name,
        "status": h.status_code,
        "timestamp": h.timestamp.isoformat(),
        "request": {
            "headers": h.request_headers,
            "body": h.request_body,
        },
        "response": {
            "headers": h.response_headers,
            "body": h.response_text,
            "time_ms": h.response_time_ms
        }
    }

@app.post("/api/history/{history_id}/resend")
async def resend_request(history_id: int, req: ResendRequest, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # 1. Get original history
    h = db.query(History).filter(History.id == history_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="History entry not found")

    # 2. Extract Data
    try:
        new_body_json = json.loads(req.new_body)
    except:
        new_body_json = {}

    # Helper to find token in body (regex or json key)
    event_token = "Unknown"
    
    # Try to find token in JSON
    if isinstance(new_body_json, dict):
        if "event_token" in new_body_json: event_token = new_body_json["event_token"]
        elif "token" in new_body_json: event_token = new_body_json["token"]
        elif "event" in new_body_json: event_token = new_body_json["event"]
    
    # If not found or body is string, try regex
    if event_token == "Unknown":
        tm = re.search(r'event_token=([a-zA-Z0-9]+)', req.new_body)
        if tm: event_token = tm.group(1)
        else:
            tm2 = re.search(r'"eventName"\s*:\s*"([^"]+)"', req.new_body)
            if tm2: event_token = tm2.group(1)
            
    # Fallback to original event name
    if event_token == "Unknown" and h.event_name:
        if "Sniper_" in h.event_name:
            event_token = h.event_name.replace("Sniper_", "")
        elif "Farm_" not in h.event_name:
            event_token = h.event_name

    # 3. Construct User Profile
    user_profile = new_body_json if isinstance(new_body_json, dict) else {}
    
    # 4. Send Event
    status_code, response_text, req_headers, req_body_final, res_headers, time_ms = await kun_engine.send_event(
        h.game_alias,
        event_token,
        user_profile
    )
    
    # 5. Create New History Record
    new_history = History(
        user_id=h.user_id,
        game_alias=h.game_alias,
        platform=h.platform,
        event_name=f"Sniper_{event_token}",
        status_code=status_code,
        request_headers=json.dumps(dict(req_headers)) if req_headers else "{}",
        request_body=req_body_final,
        response_headers=json.dumps(dict(res_headers)) if res_headers else "{}",
        response_text=response_text,
        response_time_ms=time_ms,
        timestamp=datetime.utcnow()
    )
    db.add(new_history)
    db.commit()
    db.refresh(new_history)
    
    return {
        "status": "success",
        "new_history_id": new_history.id,
        "http_code": status_code
    }

# --- New Admin Endpoints ---

@app.post("/api/users/{user_id}/balance")
async def add_balance(user_id: int, req: BalanceRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    user_obj = db.query(User).filter(User.id == user_id).first()
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_obj.balance += req.amount
    try:
        trans = Transaction(
            user_id=user_id,
            amount=req.amount,
            type="DEPOSIT",
            source="ADMIN",
            description=f"Admin recharge via dashboard by {user['username']}",
        )
        db.add(trans)
    except Exception:
        pass
    db.commit()
    
    # Notify user via bot (fire and forget)
    token = get_bot_token()
    if token:
        async def notify():
            bot = Bot(token=token)
            try:
                await bot.send_message(user_id, f"🎊 تم شحن رصيدك: `${req.amount}`")
            except: pass
            finally: await bot.session.close()
        asyncio.create_task(notify())
        
    return {"status": "success", "new_balance": user_obj.balance}

@app.post("/api/broadcast")
async def broadcast_message(req: BroadcastRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    background_tasks.add_task(send_broadcast_message, req.message, db)
    return {"status": "success", "detail": "Broadcast started in background"}

@app.post("/api/users/{user_id}/unban")
def unban_user(user_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    user_obj = db.query(User).filter(User.id == user_id).first()
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    user_obj.is_banned = False
    db.commit()
    return {"status": "success", "is_banned": False}
    
@app.post("/api/users/{user_id}/ban")
def ban_user(user_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    user_obj = db.query(User).filter(User.id == user_id).first()
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    user_obj.is_banned = True
    db.commit()
    return {"status": "success", "is_banned": True}

@app.post("/api/users/add-balance-all")
def add_balance_to_all(req: BalanceRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    try:
        # Update all users
        db.query(User).update({User.balance: User.balance + req.amount}, synchronize_session=False)
        try:
            system_user = db.query(User).filter(User.id == 0).first()
            if not system_user:
                system_user = User(id=0, balance=0.0, profile_data={"system": True})
                db.add(system_user)
                db.flush()
            trans = Transaction(
                user_id=0,
                amount=req.amount,
                type="DEPOSIT",
                source="ADMIN_BULK",
                description=f"Added {req.amount} to all users by {user['username']}",
            )
            db.add(trans)
            log = ChangeLog(
                editor=user["username"],
                action="Add Balance All",
                details={"amount": req.amount},
            )
            db.add(log)
        except Exception:
            pass
        db.commit()
        
        return {"status": "success", "message": f"Added {req.amount} to all users"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/finance/reset-expenses")
def reset_expenses(req: WalletResetConfirm, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    admin_user = db.query(WebUser).filter(WebUser.id == user["user_id"]).first()
    if not admin_user or admin_user.password_hash != hash_password(req.password):
        raise HTTPException(status_code=403, detail="Invalid password")

    try:
        total_withdrawals = db.query(func.sum(Transaction.amount)).filter(Transaction.amount < 0).scalar() or 0.0
        withdrawals_abs = abs(total_withdrawals or 0.0)

        setting = db.query(SystemSetting).filter(SystemSetting.key == "withdrawals_offset").first()
        if not setting:
            setting = SystemSetting(key="withdrawals_offset", value=withdrawals_abs)
            db.add(setting)
        else:
            setting.value = withdrawals_abs

        audit = AuditLog(
            admin_id=user["user_id"],
            action="RESET_TOTAL_WITHDRAWALS",
            details={"withdrawals_offset": withdrawals_abs},
        )
        db.add(audit)
        db.commit()
        return {"status": "success", "message": "Total withdrawals counter has been reset.", "withdrawals_offset": withdrawals_abs}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/finance/reset-currency")
def reset_currency(req: WalletResetConfirm, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    admin_user = db.query(WebUser).filter(WebUser.id == user["user_id"]).first()
    if not admin_user or admin_user.password_hash != hash_password(req.password):
        raise HTTPException(status_code=403, detail="Invalid password")

    try:
        users = db.query(User).all()
        changed = 0
        for u in users:
            if not u.profile_data:
                continue
            if "total_ops" not in u.profile_data:
                continue
            p = dict(u.profile_data)
            if p.get("total_ops") != 0:
                p["total_ops"] = 0
                u.profile_data = p
                changed += 1

        audit = AuditLog(
            admin_id=user["user_id"],
            action="RESET_CURRENCY",
            details={"users_updated": changed},
        )
        db.add(audit)
        db.commit()
        return {"status": "success", "updated": changed}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/finance/request-price")
def get_request_price(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "request_price").first()
    return {"price": float(setting.value) if setting else 0.0}

@app.post("/api/admin/finance/request-price")
def set_request_price_api(req: BalanceRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "request_price").first()
    if not setting:
        setting = SystemSetting(key="request_price", value=req.amount)
        db.add(setting)
    else:
        setting.value = req.amount
    
    # Also update the text file for compatibility with existing bot code if needed
    try:
        with open("op_price.txt", "w") as f:
            f.write(str(req.amount))
    except: pass

    log = ChangeLog(editor=user["username"], action="Update Request Price", details={"price": req.amount})
    db.add(log)
    db.commit()
    return {"status": "success", "price": req.amount}

@app.post("/api/admin/finance/set-request-price")
def set_request_price_alias(req: BalanceRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "request_price").first()
    if not setting:
        setting = SystemSetting(key="request_price", value=req.amount)
        db.add(setting)
    else:
        setting.value = req.amount

    try:
        with open("op_price.txt", "w") as f:
            f.write(str(req.amount))
    except:
        pass

    log = ChangeLog(editor=user["username"], action="Update Request Price", details={"price": req.amount})
    db.add(log)
    db.commit()
    return {"status": "success", "price": req.amount}

# --- Settings & Logs ---

@app.get("/api/settings/platforms")
def get_platform_settings(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    if not setting:
        # Default settings
        return {
            "adjust_server": True, "adjust_client": True,
            "appsflyer_server": True, "appsflyer_client": True,
            "singular_server": True, "singular_client": True
        }
    return setting.value

@app.post("/api/settings/platforms")
def update_platform_settings(req: PlatformSettings, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    if not setting:
        setting = SystemSetting(key="platforms", value=req.dict())
        db.add(setting)
    else:
        setting.value = req.dict()
    
    # Log the change
    log = ChangeLog(editor=user["username"], action="Update Platform Settings", details=req.dict())
    db.add(log)
    
    db.commit()
    return {"status": "success", "settings": req.dict()}

@app.get("/api/network/proxies")
def get_global_proxies(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
    proxies = []
    if setting and isinstance(setting.value, dict):
        proxies = setting.value.get("proxies") or []
    proxies = [str(p).strip() for p in proxies if str(p).strip()]
    return {"proxies": proxies, "masked": [_mask_proxy_url(p) for p in proxies]}

@app.post("/api/network/proxies")
def update_global_proxies(req: GlobalProxiesUpdate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    proxies = [str(p).strip() for p in (req.proxies or []) if str(p).strip()]
    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
    existing_value = setting.value if setting and isinstance(setting.value, dict) else {}
    existing_health = existing_value.get("health") if isinstance(existing_value.get("health"), dict) else {}
    existing_rotation = existing_value.get("rotation") if isinstance(existing_value.get("rotation"), dict) else {}

    value = {"proxies": proxies, "health": {}, "rotation": existing_rotation}
    for p in proxies:
        if p in existing_health:
            value["health"][p] = existing_health[p]
    if not setting:
        setting = SystemSetting(key="global_proxies", value=value)
        db.add(setting)
    else:
        setting.value = value

    log = ChangeLog(editor=user["username"], action="Update Global Proxies", details={"count": len(proxies)})
    db.add(log)
    db.commit()
    return {"status": "success", "count": len(proxies)}

@app.post("/api/network/proxies/import")
async def import_global_proxies(req: ProxyImportRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    source = (req.source or "").strip().lower()
    mode = (req.mode or "append").strip().lower()
    limit = int(req.limit or 200)
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    if mode not in ("append", "replace"):
        mode = "append"

    if source == "geonode":
        url = "https://proxylist.geonode.com/api/proxy-list?country=US&limit=500&page=1&sort_by=lastChecked&sort_type=desc"
        r = await _get_proxy_source(url)
        items = (r or {}).get("data") or []
        candidates: list[str] = []
        for it in items:
            ip = str(it.get("ip") or "").strip()
            port = str(it.get("port") or "").strip()
            prots = it.get("protocols") or []
            if not ip or not port:
                continue
            for proto in prots:
                p = str(proto or "").strip().lower()
                if p in ("http", "https", "socks4", "socks5"):
                    candidates.append(f"{p}://{ip}:{port}")
        candidates = list(dict.fromkeys(candidates))
    elif source == "proxyscrape":
        url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=us&ssl=all&anonymity=all&skip=0&limit=2000"
        text = await _get_proxy_source_text(url)
        candidates = []
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or ":" not in s:
                continue
            candidates.append(f"http://{s}")
        candidates = list(dict.fromkeys(candidates))
    else:
        raise HTTPException(status_code=400, detail="Unsupported source")

    candidates = candidates[:limit]
    if not candidates:
        return {"status": "success", "added_active": 0, "total_tested": 0, "failed": 0, "sample_errors": []}

    semaphore = asyncio.Semaphore(25)

    async def test_proxy(p: str):
        async with semaphore:
            try:
                ip = await _get_public_ip(p)
                return p, True, ip, None
            except Exception as e:
                return p, False, None, str(e)

    tested = await asyncio.gather(*[test_proxy(p) for p in candidates])
    active = [p for (p, ok, _, __) in tested if ok]
    failed = [(p, err) for (p, ok, _, err) in tested if not ok]
    sample_errors = []
    for _, err in failed:
        if not err:
            continue
        if err in sample_errors:
            continue
        sample_errors.append(err)
        if len(sample_errors) >= 3:
            break

    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
    current_value = setting.value if setting and isinstance(setting.value, dict) else {}
    current_proxies = [str(p).strip() for p in (current_value.get("proxies") or []) if str(p).strip()]
    health = current_value.get("health") if isinstance(current_value.get("health"), dict) else {}
    rotation = current_value.get("rotation") if isinstance(current_value.get("rotation"), dict) else {}

    if mode == "replace":
        new_proxies = list(dict.fromkeys(active))
        rotation = {}
    else:
        new_proxies = list(dict.fromkeys(current_proxies + active))

    now = datetime.utcnow().isoformat()
    new_health = {}
    for p in new_proxies:
        if p in health:
            new_health[p] = health[p]
    for (p, ok, ip, err) in tested:
        if p not in new_proxies:
            continue
        new_health[p] = {"alive": bool(ok), "ip": ip, "error": err, "last_checked": now}

    value = {"proxies": new_proxies, "health": new_health, "rotation": rotation}
    if not setting:
        setting = SystemSetting(key="global_proxies", value=value)
        db.add(setting)
    else:
        setting.value = value

    db.add(ChangeLog(editor=user["username"], action="Import Global Proxies", details={"source": source, "added_active": len(active), "mode": mode}))
    db.commit()
    return {
        "status": "success",
        "added_active": len(active),
        "total_tested": len(candidates),
        "failed": len(failed),
        "sample_errors": sample_errors,
        "total_now": len(new_proxies),
    }

@app.get("/api/admin/store/channels")
def get_store_channels(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    ids: list[int] = []

    setting = db.query(SystemSetting).filter(SystemSetting.key == "store_channels").first()
    if setting:
        v = setting.value
        if isinstance(v, dict):
            v = v.get("channels") or []
        if isinstance(v, list):
            for x in v:
                try:
                    ids.append(int(x))
                except Exception:
                    continue

    legacy = db.query(SystemSetting).filter(SystemSetting.key == "store_channel_ids").first()
    if legacy and isinstance(legacy.value, list):
        for x in legacy.value:
            try:
                ids.append(int(x))
            except Exception:
                continue

    ids = list(dict.fromkeys([i for i in ids if i != 0]))
    return {"channels": ids, "channel_ids": ids}

@app.post("/api/admin/store/channels")
def set_store_channels(req: StoreChannelsUpdate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    raw = req.channels if req.channels is not None else (req.channel_ids if req.channel_ids is not None else [])
    ids: list[int] = []
    for x in raw or []:
        try:
            ids.append(int(x))
        except Exception:
            continue
    ids = list(dict.fromkeys([i for i in ids if i != 0]))

    setting = db.query(SystemSetting).filter(SystemSetting.key == "store_channels").first()
    value = {"channels": ids}
    if not setting:
        setting = SystemSetting(key="store_channels", value=value)
        db.add(setting)
    else:
        setting.value = value

    legacy = db.query(SystemSetting).filter(SystemSetting.key == "store_channel_ids").first()
    if not legacy:
        legacy = SystemSetting(key="store_channel_ids", value=ids)
        db.add(legacy)
    else:
        legacy.value = ids

    db.add(ChangeLog(editor=user["username"], action="Update Store Channels", details={"count": len(ids), "channels": ids}))
    db.commit()
    return {"status": "success", "count": len(ids), "channels": ids, "channel_ids": ids}

def _extract_username_from_link(link: str) -> str | None:
    s = (link or "").strip()
    if not s:
        return None
    s = s.replace("https://", "").replace("http://", "")
    if s.startswith("t.me/"):
        path = s.split("t.me/", 1)[1]
    elif s.startswith("telegram.me/"):
        path = s.split("telegram.me/", 1)[1]
    else:
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = path.strip("/")
    if not path:
        return None
    if path.startswith("+") or path.startswith("joinchat/"):
        return None
    if path.startswith("c/"):
        return None
    username = path.split("/", 1)[0].strip()
    if not username:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_]{5,}", username):
        return None
    return username

def _extract_internal_id_from_tme_c(link: str) -> int | None:
    s = (link or "").strip()
    if not s:
        return None
    s = s.replace("https://", "").replace("http://", "")
    if "t.me/c/" not in s:
        return None
    try:
        path = s.split("t.me/c/", 1)[1]
        internal = path.split("/", 1)[0].strip()
        if not internal.isdigit():
            return None
        return int(f"-100{internal}")
    except Exception:
        return None

@app.get("/api/admin/store/resolve")
async def resolve_store_channel(username: Optional[str] = None, link: Optional[str] = None, user: dict = Depends(require_admin)):
    token = get_bot_token()
    if not token:
        raise HTTPException(status_code=400, detail="BOT_TOKEN غير مضبوط على الخادم")

    u = (username or "").strip().lstrip("@")
    lnk = (link or "").strip()

    if not u and not lnk:
        raise HTTPException(status_code=400, detail="username أو link مطلوب")

    bot = Bot(token=token)
    try:
        chat_id: int | None = None
        source = None

        if u:
            source = "username"
            chat = await bot.get_chat(f"@{u}")
            chat_id = int(chat.id)
            return {"id": chat_id, "title": getattr(chat, "title", None), "username": getattr(chat, "username", None), "type": getattr(chat, "type", None), "source": source}

        internal_id = _extract_internal_id_from_tme_c(lnk)
        if internal_id is not None:
            source = "tme_c"
            try:
                chat = await bot.get_chat(internal_id)
                return {"id": int(chat.id), "title": getattr(chat, "title", None), "username": getattr(chat, "username", None), "type": getattr(chat, "type", None), "source": source}
            except Exception:
                return {"id": int(internal_id), "title": None, "username": None, "type": None, "source": source}

        u2 = _extract_username_from_link(lnk)
        if u2:
            source = "link_username"
            chat = await bot.get_chat(f"@{u2}")
            chat_id = int(chat.id)
            return {"id": chat_id, "title": getattr(chat, "title", None), "username": getattr(chat, "username", None), "type": getattr(chat, "type", None), "source": source}

        if "t.me/+" in lnk or "t.me/joinchat/" in lnk or "joinchat/" in lnk:
            raise HTTPException(status_code=400, detail="لا يمكن استخراج ID من رابط دعوة خاص عبر Bot API. استخدم @يوزر القناة أو -100... بعد إضافة البوت.")

        raise HTTPException(status_code=400, detail="صيغة الرابط غير مدعومة")
    finally:
        await bot.session.close()

@app.get("/api/admin/store/channels/log")
def get_store_channels_log(limit: int = 50, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    limit = max(1, min(int(limit or 50), 200))
    rows = (
        db.query(ChangeLog)
        .filter(ChangeLog.action == "Update Store Channels")
        .order_by(ChangeLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        details = r.details if isinstance(r.details, dict) else {}
        channels = details.get("channels") if isinstance(details.get("channels"), list) else []
        out.append(
            {
                "id": r.id,
                "time": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "editor": r.editor,
                "count": int(details.get("count") or (len(channels) if channels else 0)),
                "channels": [int(x) for x in channels if str(x).strip().lstrip("-").isdigit()],
            }
        )
    return out

@app.get("/api/admin/store/items/debug")
def debug_store_items(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    total = db.query(func.count(StoreItem.id)).scalar() or 0
    last = db.query(StoreItem).order_by(StoreItem.created_at.desc()).limit(5).all()
    return {
        "total": int(total),
        "last": [
            {
                "id": it.id,
                "title": it.title,
                "is_active": it.is_active,
                "source_chat_id": it.source_chat_id,
                "source_message_id": it.source_message_id,
                "file_type": it.file_type,
                "created_at": it.created_at,
            }
            for it in last
        ],
    }

@app.post("/api/admin/store/scan")
async def scan_store_channels(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    token = get_bot_token()
    if not token:
        raise HTTPException(status_code=400, detail="BOT_TOKEN غير مضبوط على الخادم")

    channels: list[int] = []
    setting = db.query(SystemSetting).filter(SystemSetting.key == "store_channels").first()
    if setting:
        v = setting.value
        if isinstance(v, dict):
            v = v.get("channels") or []
        if isinstance(v, list):
            for x in v:
                try:
                    channels.append(int(x))
                except Exception:
                    continue

    channels = list(dict.fromkeys([c for c in channels if c != 0]))
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        results = []
        ok = 0
        failed = 0
        for cid in channels:
            try:
                chat = await bot.get_chat(cid)
                member_status = None
                member_can_post = None
                member_can_delete = None
                try:
                    m = await bot.get_chat_member(cid, me.id)
                    member_status = getattr(m, "status", None)
                    priv = getattr(m, "privileges", None)
                    if priv is not None:
                        member_can_post = getattr(priv, "can_post_messages", None)
                        member_can_delete = getattr(priv, "can_delete_messages", None)
                except Exception:
                    pass
                ok += 1
                results.append({
                    "id": int(cid),
                    "ok": True,
                    "title": getattr(chat, "title", None),
                    "username": getattr(chat, "username", None),
                    "type": getattr(chat, "type", None),
                    "member_status": member_status,
                    "can_post": member_can_post,
                    "can_delete": member_can_delete,
                    "error": None
                })
            except Exception as e:
                failed += 1
                results.append({
                    "id": int(cid),
                    "ok": False,
                    "title": None,
                    "username": None,
                    "type": None,
                    "member_status": None,
                    "can_post": None,
                    "can_delete": None,
                    "error": str(e)
                })
        return {
            "bot": {"id": me.id, "username": me.username},
            "total": len(channels),
            "ok": ok,
            "failed": failed,
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scan_failed: {type(e).__name__}: {e}")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass

@app.post("/api/admin/store/test")
async def test_store_ingest(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    token = get_bot_token()
    if not token:
        raise HTTPException(status_code=400, detail="BOT_TOKEN غير مضبوط على الخادم")
    tokens_env = [t.strip() for t in (os.getenv("BOT_TOKENS") or "").split(",") if t.strip()]
    sender_token = None
    if len(tokens_env) > 1 and tokens_env[1] and tokens_env[1] != token:
        sender_token = tokens_env[1]

    channels: list[int] = []
    setting = db.query(SystemSetting).filter(SystemSetting.key == "store_channels").first()
    if setting:
        v = setting.value
        if isinstance(v, dict):
            v = v.get("channels") or []
        if isinstance(v, list):
            for x in v:
                try:
                    channels.append(int(x))
                except Exception:
                    continue
    channels = list(dict.fromkeys([c for c in channels if c != 0]))

    bot = Bot(token=token)
    sender_bot = None
    sender_me = None
    try:
        me = await bot.get_me()
        if sender_token:
            try:
                sender_bot = Bot(token=sender_token)
                sender_me = await sender_bot.get_me()
            except Exception:
                if sender_bot:
                    try:
                        await sender_bot.session.close()
                    except Exception:
                        pass
                sender_bot = None
                sender_me = None

        ok = 0
        failed = 0
        results = []
        for cid in channels:
            try:
                if sender_bot:
                    await sender_bot.send_photo(cid, "https://placehold.co/256x256/png?text=Store+Test")
                else:
                    await bot.send_photo(cid, "https://placehold.co/256x256/png?text=Store+Test")
                ok += 1
                results.append({"id": int(cid), "ok": True, "error": None})
            except Exception as e:
                failed += 1
                results.append({"id": int(cid), "ok": False, "error": str(e)})
        return {
            "bot": {"id": me.id, "username": me.username},
            "sender_bot": {"id": sender_me.id, "username": sender_me.username} if sender_me else None,
            "total": len(channels),
            "ok": ok,
            "failed": failed,
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"test_failed: {type(e).__name__}: {e}")
    finally:
        if sender_bot:
            try:
                await sender_bot.session.close()
            except Exception:
                pass
        try:
            await bot.session.close()
        except Exception:
            pass

@app.get("/api/admin/store/categories")
def list_store_categories(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    _ensure_default_store_categories(db)
    rows = db.query(StoreCategory).order_by(StoreCategory.sort_order.asc(), StoreCategory.id.asc()).all()
    out = []
    for c in rows:
        count = db.query(func.count(StoreItem.id)).filter(StoreItem.category_id == c.id).scalar() or 0
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "slug": c.slug,
                "sort_order": c.sort_order,
                "is_active": c.is_active,
                "items": int(count),
                "created_at": c.created_at,
            }
        )
    return out

@app.post("/api/admin/store/categories")
def create_store_category(req: StoreCategoryCreate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    slug = (req.slug or "").strip().lower()
    if not slug:
        slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not slug:
        slug = secrets.token_hex(4)
    if db.query(StoreCategory).filter(StoreCategory.slug == slug).first():
        slug = f"{slug}_{secrets.token_hex(2)}"

    cat = StoreCategory(name=name[:120], slug=slug[:120], sort_order=int(req.sort_order or 0), is_active=bool(req.is_active))
    db.add(cat)
    db.add(ChangeLog(editor=user["username"], action="Create Store Category", details={"slug": slug}))
    db.commit()
    db.refresh(cat)
    return {"status": "success", "id": cat.id}

@app.patch("/api/admin/store/categories/{category_id}")
def update_store_category(category_id: int, req: StoreCategoryUpdate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    cat = db.query(StoreCategory).filter(StoreCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    if req.name is not None:
        name = (req.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name required")
        cat.name = name[:120]
    if req.slug is not None:
        slug = (req.slug or "").strip().lower()
        if not slug:
            raise HTTPException(status_code=400, detail="Slug required")
        if slug != cat.slug and db.query(StoreCategory).filter(StoreCategory.slug == slug).first():
            raise HTTPException(status_code=400, detail="Slug exists")
        cat.slug = slug[:120]
    if req.sort_order is not None:
        cat.sort_order = int(req.sort_order)
    if req.is_active is not None:
        cat.is_active = bool(req.is_active)

    db.add(ChangeLog(editor=user["username"], action="Update Store Category", details={"id": category_id}))
    db.commit()
    return {"status": "success"}

@app.get("/api/admin/store/items")
def list_store_items(
    status: str = "unpublished",
    category_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin),
):
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    is_active = True if (status or "").lower() == "published" else False

    query = db.query(StoreItem).filter(StoreItem.is_active == is_active)
    if category_id is not None:
        query = query.filter(StoreItem.category_id == int(category_id))
    if q:
        qq = f"%{q.strip()}%"
        query = query.filter(StoreItem.title.ilike(qq))

    total = query.count()
    rows = query.order_by(StoreItem.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "data": [
            {
                "id": it.id,
                "category_id": it.category_id,
                "title": it.title,
                "description": it.description,
                "price": it.price,
                "is_active": it.is_active,
                "source_chat_id": it.source_chat_id,
                "source_message_id": it.source_message_id,
                "file_type": it.file_type,
                "file_name": it.file_name,
                "created_at": it.created_at,
            }
            for it in rows
        ],
    }

@app.patch("/api/admin/store/items/{item_id}")
def update_store_item(item_id: int, req: StoreItemUpdate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    it = db.query(StoreItem).filter(StoreItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")

    if req.title is not None:
        title = (req.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title required")
        it.title = title[:200]
    if req.description is not None:
        it.description = (req.description or "").strip() or None
    if req.category_id is not None:
        cid = int(req.category_id) if req.category_id else None
        if cid is not None:
            cat = db.query(StoreCategory).filter(StoreCategory.id == cid).first()
            if not cat:
                raise HTTPException(status_code=400, detail="Category invalid")
        it.category_id = cid
    if req.price is not None:
        price = float(req.price or 0.0)
        if price < 0:
            price = 0.0
        it.price = price
    if req.is_active is not None:
        if bool(req.is_active) and (not it.category_id or float(it.price or 0.0) <= 0):
            raise HTTPException(status_code=400, detail="Set category and price first")
        it.is_active = bool(req.is_active)

    db.add(ChangeLog(editor=user["username"], action="Update Store Item", details={"id": item_id}))
    db.commit()
    return {"status": "success"}

@app.get("/api/admin/store/items/{item_id}")
def get_store_item(item_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    it = db.query(StoreItem).filter(StoreItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    return {
        "id": it.id,
        "category_id": it.category_id,
        "title": it.title,
        "description": it.description,
        "price": it.price,
        "is_active": it.is_active,
        "source_chat_id": it.source_chat_id,
        "source_message_id": it.source_message_id,
        "file_type": it.file_type,
        "file_name": it.file_name,
        "created_at": it.created_at,
    }

@app.delete("/api/admin/store/items/{item_id}")
def delete_store_item(item_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    it = db.query(StoreItem).filter(StoreItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    purchased = db.query(func.count(StorePurchase.id)).filter(StorePurchase.item_id == item_id).scalar() or 0
    if int(purchased) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete purchased item")
    db.delete(it)
    db.add(ChangeLog(editor=user["username"], action="Delete Store Item", details={"id": item_id}))
    db.commit()
    return {"status": "success"}

@app.get("/api/admin/store/items/{item_id}/preview")
async def preview_store_item(item_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    it = db.query(StoreItem).filter(StoreItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")
    token = get_bot_token()
    if not token:
        raise HTTPException(status_code=400, detail="BOT_TOKEN غير مضبوط على الخادم")

    file_type = (it.file_type or "").lower()
    media_type = "application/octet-stream"
    if file_type == "photo":
        media_type = "image/jpeg"
    elif file_type == "video":
        media_type = "video/mp4"
    elif file_type == "audio":
        media_type = "audio/mpeg"
    elif file_type == "voice":
        media_type = "audio/ogg"

    bot = Bot(token=token)
    try:
        f = await bot.get_file(it.file_id)
        file_path = getattr(f, "file_path", None)
        if not file_path:
            raise HTTPException(status_code=500, detail="No file_path")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass

    url = f"https://api.telegram.org/file/bot{token}/{file_path}"

    async def _iter_bytes():
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(_iter_bytes(), media_type=media_type)

@app.get("/api/admin/store/purchases")
def list_store_purchases(
    user_id: Optional[int] = None,
    item_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin),
):
    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))
    query = db.query(StorePurchase)
    if user_id is not None:
        query = query.filter(StorePurchase.user_id == int(user_id))
    if item_id is not None:
        query = query.filter(StorePurchase.item_id == int(item_id))
    total = query.count()
    rows = query.order_by(StorePurchase.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "data": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "item_id": p.item_id,
                "price_paid": p.price_paid,
                "created_at": p.created_at,
            }
            for p in rows
        ],
    }

@app.get("/api/network/status")
async def get_network_status(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    setting = db.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
    proxies = []
    if setting and isinstance(setting.value, dict):
        proxies = setting.value.get("proxies") or []
    proxies = [str(p).strip() for p in proxies if str(p).strip()]

    direct_ip = None
    try:
        direct_ip = await _get_public_ip()
    except Exception:
        direct_ip = None

    async def probe(p: str):
        try:
            ip = await _get_public_ip(p)
            return {"proxy": p, "masked": _mask_proxy_url(p), "ip": ip, "ok": True, "error": None}
        except Exception as e:
            # If SOCKS5 host unreachable for domain, try suggesting DNS issue
            err_msg = str(e)
            if "socks" in p and ("Host unreachable" in err_msg or "0x04" in err_msg):
                err_msg += " (Note: This SOCKS proxy may fail to resolve domains. Try using IP addresses if possible, or check proxy DNS settings.)"
            return {"proxy": p, "masked": _mask_proxy_url(p), "ip": None, "ok": False, "error": err_msg}

    results = await asyncio.gather(*[probe(p) for p in proxies]) if proxies else []

    try:
        value = setting.value if setting and isinstance(setting.value, dict) else {}
        health = value.get("health") if isinstance(value.get("health"), dict) else {}
        now = datetime.utcnow().isoformat()
        for r in results:
            p = r.get("proxy")
            if not p:
                continue
            health[p] = {"alive": bool(r.get("ok")), "ip": r.get("ip"), "error": r.get("error"), "last_checked": now}
        value["health"] = health
        value["proxies"] = proxies
        if setting:
            setting.value = value
            db.commit()
    except Exception:
        pass

    return {"direct_ip": direct_ip, "proxies": results}

@app.get("/api/changelogs")
def get_changelogs(limit: int = 50, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    logs = db.query(ChangeLog).order_by(ChangeLog.timestamp.desc()).limit(limit).all()
    return [{"id": l.id, "editor": l.editor, "action": l.action, "details": l.details, "time": l.timestamp.strftime("%Y-%m-%d %H:%M:%S")} for l in logs]

@app.post("/api/games")
def create_game(req: GameCreate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    alias = req.name.lower().replace(" ", "_")
    if db.query(Game).filter(Game.alias == alias).first():
        raise HTTPException(status_code=400, detail="Game alias already exists")
    
    json_data = {
        "app_id": req.package_name,
        "provider": req.provider,
        "device_os": (req.device_os or "android").lower()
    }
    
    if req.provider == "AppsFlyer":
        if not req.main_key:
             raise HTTPException(status_code=400, detail="Dev Key is required for AppsFlyer")
        if len(req.main_key) < 5:
            raise HTTPException(status_code=400, detail="Invalid Dev Key for AppsFlyer")
            
        json_data["dev_key"] = req.main_key
        template_str = "{\"af_level\":\"{LEVEL}\",\"af_score\":100}" if req.advanced_data.lower() == "default" else req.advanced_data
        json_data["event_templates"] = {
            "level_up": {
                "event_name": "af_level_achieved",
                "json_template": template_str
            }
        }
    elif req.provider == "Adjust":
        if not req.main_key:
             raise HTTPException(status_code=400, detail="App Token is required for Adjust")
        if len(req.main_key) != 12 or not req.main_key.isalnum():
             raise HTTPException(status_code=400, detail="Adjust App Token must be 12 alphanumeric characters")
             
        json_data["app_token"] = req.main_key
        json_data["level_sequence"] = []
        lines = req.advanced_data.split('\n')
        for line in lines:
            if ":" in line:
                parts = line.split(":")
                json_data["level_sequence"].append({"lvl": parts[0].strip(), "tkn": parts[1].strip()})
    elif req.provider == "Singular":
        if not req.main_key:
             raise HTTPException(status_code=400, detail="API Key is required for Singular")
        if len(req.main_key) < 20:
             raise HTTPException(status_code=400, detail="Invalid API Key for Singular")
             
        json_data["api_key"] = req.main_key
        json_data["secret"] = req.advanced_data # Using advanced_data for Secret/Other config
    
    new_game = Game(
        alias=alias,
        name=req.name,
        package_name=req.package_name,
        device_os=(req.device_os or "android").lower(),
        provider=req.provider,
        json_data=json_data,
        is_active=True,
        price=req.price
    )
    db.add(new_game)
    db.commit()
    
    # Log creation
    log = ChangeLog(editor=user["username"], action="Create Game", details={"name": req.name, "provider": req.provider})
    db.add(log)
    db.commit()
    
    return {"status": "success", "game_id": new_game.id}


@app.post("/api/games/{game_id}/promote")
def promote_game(game_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Allow if Admin OR Owner
    if user["role"] != "admin" and game.owner_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    game.owner_id = None # Make Global
    db.commit()
    
    log = ChangeLog(editor=user["username"], action="Promote Game", details={"game_id": game_id, "name": game.name})
    db.add(log)
    db.commit()
    
    return {"status": "success", "owner_id": None}

@app.get("/api/games/{game_id}")
def get_game(game_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {
        "id": game.id,
        "name": game.name,
        "alias": game.alias,
        "package_name": game.package_name,
        "provider": game.provider,
        "is_active": game.is_active,
        "price": game.price,
        "json_data": game.json_data
    }

@app.put("/api/games/{game_id}")
def update_game(game_id: int, req: GameUpdate, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Allow if Admin OR Owner
    if user["role"] != "admin" and game.owner_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    
    try:
        new_config = json.loads(req.json_configuration)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON configuration")
    
    # Check alias uniqueness if changed
    if req.alias != game.alias:
        if db.query(Game).filter(Game.alias == req.alias).first():
             raise HTTPException(status_code=400, detail="Game Key (Alias) already exists")

    game.name = req.name
    game.alias = req.alias # Update alias
    game.package_name = req.package_name
    game.provider = req.provider
    game.json_data = new_config
    game.is_active = req.is_active
    game.price = req.price
    
    db.commit()
    
    log = ChangeLog(editor=user["username"], action="Update Game", details={"game_id": game_id, "name": req.name})
    db.add(log)
    db.commit()
    
    return {"status": "success"}

@app.delete("/api/games/{game_id}")
def delete_game(game_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Allow if Admin OR Owner
    if user["role"] != "admin" and game.owner_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    db.delete(game)
    db.commit()
    
    log = ChangeLog(editor=user["username"], action="Delete Game", details={"game_id": game_id, "name": game.name})
    db.add(log)
    db.commit()
    
    return {"status": "success"}

@app.get("/api/games/{game_id}/timelines")
def get_timelines(game_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    timelines = db.query(GameTimeline).filter(GameTimeline.game_id == game_id).all()
    return [{
        "step_name": t.step_name,
        "event_value": t.event_value or t.event_token,
        "day_offset": t.day_offset,
        "hour_offset": t.hour_offset
    } for t in timelines]

@app.post("/api/games/{game_id}/timelines")
def update_timelines(game_id: int, req: TimelineUpdate, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    # Clear existing timelines
    db.query(GameTimeline).filter(GameTimeline.game_id == game_id).delete()
    
    # Add new ones
    for step in req.steps:
        new_tl = GameTimeline(
            game_id=game_id,
            step_name=step.step_name,
            event_value=step.event_value,
            event_token=step.event_value, # Save to both for compatibility
            day_offset=step.day_offset,
            hour_offset=step.hour_offset
        )
        db.add(new_tl)
    
    db.commit()
    return {"status": "success", "count": len(req.steps)}

@app.get("/api/settings/price")
def get_price(user: dict = Depends(get_current_user)):
    try:
        if os.path.exists("op_price.txt"):
            with open("op_price.txt", "r") as f:
                return {"price": float(f.read().strip())}
    except: pass
    return {"price": 0.05}

@app.post("/api/settings/price")
def set_price(req: PriceRequest, user: dict = Depends(require_admin)):
    try:
        with open("op_price.txt", "w") as f:
            f.write(str(req.price))
        return {"status": "success", "new_price": req.price}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/coupons")
def get_coupons(db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    # Return active coupons (not used)
    coupons = db.query(Coupon).filter(Coupon.is_used == False).all()
    return [{"id": c.id, "code": c.code, "amount": c.amount, "created_at": c.created_at.strftime("%Y-%m-%d %H:%M")} for c in coupons]

@app.delete("/api/coupons/{coupon_id}")
def delete_coupon(coupon_id: int, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    db.query(Coupon).filter(Coupon.id == coupon_id).delete()
    db.commit()
    return {"status": "success"}

@app.post("/api/coupons")
def generate_coupon(req: CouponRequest, db: Session = Depends(get_db), user: dict = Depends(require_admin)):
    code = f"KUN-{secrets.token_hex(4).upper()}"
    new_coupon = Coupon(code=code, amount=req.amount)
    db.add(new_coupon)
    db.commit()
    return {"status": "success", "code": code, "amount": req.amount}

@app.post("/api/backup")
async def trigger_backup(background_tasks: BackgroundTasks, user: dict = Depends(require_admin)):
    admin_id = os.getenv("ADMIN_ID")
    if not admin_id:
        raise HTTPException(status_code=400, detail="ADMIN_ID not set in .env")
    
    try:
        aid = int(admin_id)
        background_tasks.add_task(perform_manual_backup, aid)
        return {"status": "success", "detail": "Backup started"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ADMIN_ID")

# Direct backup download (generates a ZIP and streams it)
@app.get("/api/backup/download")
async def download_backup(user: dict = Depends(require_admin)):
    if not create_project_backup:
        raise HTTPException(status_code=500, detail="Backup utility not available")
    zip_path = await create_project_backup()
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=500, detail="Failed to create backup")
    headers = {
        "Content-Disposition": f'attachment; filename="{os.path.basename(zip_path)}"'
    }
    return FileResponse(zip_path, media_type="application/zip", headers=headers)

# Health check endpoint
_app_started_at = datetime.utcnow()

@app.get("/api/health")
def health_status(db: Session = Depends(SessionLocal), user: dict = Depends(require_admin)):
    try:
        users = db.query(func.count(User.id)).scalar() or 0
        games = db.query(func.count(Game.id)).scalar() or 0
        events_24h = db.query(func.count(History.id)).filter(
            History.timestamp >= datetime.utcnow() - timedelta(hours=24)
        ).scalar() or 0
        load_info = {
            "uptime_seconds": (datetime.utcnow() - _app_started_at).total_seconds(),
            "users": users,
            "games": games,
            "events_24h": events_24h
        }
        return {"status": "ok", "data": load_info}
    finally:
        db.close()

# Maintenance mode setting
class MaintenanceToggle(BaseModel):
    enabled: bool

@app.get("/api/maintenance")
def get_maintenance(db: Session = Depends(SessionLocal), user: dict = Depends(require_admin)):
    try:
        setting = db.query(SystemSetting).filter(SystemSetting.key == "maintenance_mode").first()
        val = False
        if setting and setting.value is not None:
            try:
                if isinstance(setting.value, dict):
                    val = bool(setting.value.get("enabled", False))
                else:
                    val = bool(setting.value)
            except Exception:
                val = False
        return {"enabled": val}
    finally:
        db.close()

@app.post("/api/maintenance")
def set_maintenance(req: MaintenanceToggle, db: Session = Depends(SessionLocal), user: dict = Depends(require_admin)):
    try:
        setting = db.query(SystemSetting).filter(SystemSetting.key == "maintenance_mode").first()
        if not setting:
            setting = SystemSetting(key="maintenance_mode", value={"enabled": req.enabled})
            db.add(setting)
        else:
            setting.value = {"enabled": req.enabled}
        db.add(ChangeLog(editor=str(user["username"]), action="Maintenance Toggle", details={"enabled": req.enabled}))
        db.commit()
        return {"status": "success", "enabled": req.enabled}
    finally:
        db.close()

# Audit log list
@app.get("/api/audit")
def list_audit(limit: int = 50, offset: int = 0, db: Session = Depends(SessionLocal), user: dict = Depends(require_admin)):
    try:
        q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        data = []
        for r in rows:
            data.append({
                "id": r.id,
                "admin_id": r.admin_id,
                "action": r.action,
                "details": r.details,
                "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M")
            })
        return {"total": total, "data": data}
    finally:
        db.close()

# --- WebSocket Endpoint ---
@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # نبقي الاتصال مفتوحاً فقط
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
