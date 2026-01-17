from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, JSON, Enum, ForeignKey, DateTime, text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import enum
import os
from pathlib import Path

Base = declarative_base()

# أبقينا الكلاس كمرجع، لكن سنستخدم String في الجدول لمرونة أكبر
class ProviderType(enum.Enum):
    APPSFLYER = "AppsFlyer"
    ADJUST = "Adjust"
    SINGULAR = "Singular"
    CUSTOM = "Custom"

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)  # Telegram ID
    balance = Column(Float, default=0.0)
    is_banned = Column(Boolean, default=False)
    # ProfileData: {gaid, af_id, adid, android_id, ua, ip, proxy, etc.}
    profile_data = Column(JSON, default={})
    last_active = Column(DateTime, default=datetime.datetime.utcnow)
    
    # علاقات
    tasks = relationship("Task", back_populates="user")
    history = relationship("History", back_populates="user")
    custom_plans = relationship("UserCustomPlan", back_populates="user")
    owned_games = relationship("Game", back_populates="owner")
    transactions = relationship("Transaction", back_populates="user")

class Transaction(Base):
    """سجل العمليات المالية (Recharge/Spend)"""
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    amount = Column(Float, nullable=False) # موجب = شحن، سالب = خصم
    type = Column(String, nullable=False) # DEPOSIT, WITHDRAWAL, RESET
    source = Column(String, nullable=False) # COUPON, ADMIN, GAME_COST, RESET_ALL
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="transactions")

class Game(Base):
    __tablename__ = 'games'
    id = Column(Integer, primary_key=True)
    alias = Column(String, unique=True) # الاسم المختصر (game_key)
    name = Column(String)
    package_name = Column(String)
    device_os = Column(String, default="android")
    
    # تم التعديل إلى String لتجنب مشاكل الهجرة من ملف JSON
    provider = Column(String, default="AppsFlyer")
    
    # --- الإضافة الجديدة (المهمة جداً للمرحلة 1) ---
    # سيحتوي هذا العمود على كامل إعدادات اللعبة المنقولة من الكونفيغ
    # {dev_key, app_id, event_templates, level_sequence, etc.}
    json_data = Column(JSON, default={}) 
    
    price = Column(Float, default=0.0) # تكلفة الشحن/اللعبة
    
    # أبقينا هذه الأعمدة للحفاظ على هيكلية كودك القديم وعدم فقدان شيء
    config_data = Column(JSON, default={})
    events_list = Column(JSON, default=[])
    
    is_active = Column(Boolean, default=True)

    # Owner (User ID) - Null for Global Games, Set for Private Games
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    owner = relationship("User", back_populates="owned_games")

    # علاقة مع الخطط الزمنية الرسمية
    timelines = relationship("GameTimeline", back_populates="game", cascade="all, delete-orphan")

class GameTimeline(Base):
    """المخطط الزمني الافتراضي الذي يضعه المدير للعبة"""
    __tablename__ = 'game_timelines'
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey('games.id'))
    step_name = Column(String) # مثلاً Board 10
    event_token = Column(String, nullable=True) # لـ Adjust
    event_value = Column(String, nullable=True) # لـ AF
    
    # التوقيت النسبي (تراكمي)
    day_offset = Column(Integer, default=0)
    hour_offset = Column(Integer, default=0)
    minute_offset = Column(Integer, default=0)

    game = relationship("Game", back_populates="timelines")

class UserCustomPlan(Base):
    """الخطة المخصصة التي ينشئها المستخدم لنفسه"""
    __tablename__ = 'user_custom_plans'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    plan_name = Column(String) # اسم يسميه المستخدم لخطته
    # خطوات الخطة مخزنة بصيغة JSON لمرونة التعديل يدوياً من المستخدم
    # Format: [{"step": "Lvl 10", "delay_hours": 24, "token": "xxx"}, ...]
    steps_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="custom_plans")

class Task(Base):
    """المهام المجدولة (Time Warp / Scheduled Farm / Natural Path)"""
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    game_id = Column(Integer, ForeignKey('games.id'))
    target_time = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="PENDING")  # PENDING, RUNNING, DONE, FAILED
    task_type = Column(String, default="SINGLE") # SINGLE, FARM, NATURAL_PATH
    
    # Payload: {event_name, level, gaid, af_id, etc.}
    payload = Column(JSON)
    
    user = relationship("User", back_populates="tasks")

class History(Base):
    """سجل العمليات الناجحة (Logs for Billing & Analytics)"""
    __tablename__ = 'history'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    game_alias = Column(String)
    platform = Column(String)
    event_name = Column(String)
    status_code = Column(Integer)
    response_text = Column(String)
    
    # New Detailed Logging Fields
    request_headers = Column(JSON, default={})
    request_body = Column(String, default="")
    response_headers = Column(JSON, default={})
    response_time_ms = Column(Float, default=0.0)
    
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="history")

class SystemSetting(Base):
    """إعدادات النظام العامة (مثل تفعيل/تعطيل المنصات)"""
    __tablename__ = 'system_settings'
    key = Column(String, primary_key=True)
    value = Column(JSON) # يمكن أن يكون قيمة بسيطة أو كائن معقد
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class ChangeLog(Base):
    """سجل التغييرات الإدارية"""
    __tablename__ = 'change_logs'
    id = Column(Integer, primary_key=True)
    editor = Column(String) # اسم المحرر أو "Admin"
    action = Column(String) # نوع التغيير (تعديل لعبة، تغيير إعدادات)
    details = Column(JSON) # التفاصيل القديمة والجديدة
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class AuditLog(Base):
    """سجل تدقيق العمليات الحساسة"""
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey('web_users.id'), nullable=True) # أو يمكن أن يكون users.id
    action = Column(String, nullable=False)
    details = Column(JSON, nullable=True) # تفاصيل العملية أو Snapshot
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class Coupon(Base):
    """جدول الكوبونات وأكواد الشحن"""
    __tablename__ = 'coupons'
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False) 
    amount = Column(Float, nullable=False) 
    is_used = Column(Boolean, default=False) 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    used_by = Column(Integer, nullable=True) 

class WebUser(Base):
    """مستخدمي لوحة التحكم (لصلاحيات الوصول)"""
    __tablename__ = 'web_users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="user") # admin, moderator, user
    last_login = Column(DateTime)

class BotToken(Base):
    """إدارة توكنات البوتات المتعددة"""
    __tablename__ = 'bot_tokens'
    id = Column(Integer, primary_key=True)
    name = Column(String) # اسم وصفي للبوت
    token = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class BotAccess(Base):
    """صلاحيات الوصول للبوت (Telegram IDs)"""
    __tablename__ = 'bot_access'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False) # Telegram ID
    role = Column(String, default="editor") # admin, editor
    name = Column(String) # اسم وصفي (اختياري)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class ChatConversation(Base):
    __tablename__ = 'chat_conversations'
    id = Column(Integer, primary_key=True)
    kind = Column(String, default="user_admin")  # user_admin, user_user
    user_a_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    user_b_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    is_closed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('chat_conversations.id'), nullable=False)
    sender_role = Column(String, default="user")  # user, admin, system
    sender_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    body = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    conversation = relationship("ChatConversation", back_populates="messages")

class StoreCategory(Base):
    __tablename__ = "store_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    items = relationship("StoreItem", back_populates="category")

class StoreItem(Base):
    __tablename__ = "store_items"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("store_categories.id"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, default=0.0)
    is_active = Column(Boolean, default=False)

    source_chat_id = Column(Integer, nullable=False)
    source_message_id = Column(Integer, nullable=False)
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=True)
    file_type = Column(String, nullable=False)  # document, video, photo, audio, voice
    file_name = Column(String, nullable=True)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (UniqueConstraint("source_chat_id", "source_message_id", name="uq_store_items_source"),)

    category = relationship("StoreCategory", back_populates="items")
    purchases = relationship("StorePurchase", back_populates="item")

class StorePurchase(Base):
    __tablename__ = "store_purchases"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("store_items.id"), nullable=False)
    price_paid = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_store_purchase_user_item"),)

    user = relationship("User")
    item = relationship("StoreItem", back_populates="purchases")

# إعداد قاعدة البيانات
_root_dir = Path(__file__).resolve().parent.parent
_db_path = os.getenv("DATABASE_PATH") or str(_root_dir / "kun_nexus.db")
_db_uri = f"sqlite:///{Path(_db_path).resolve().as_posix()}"
engine = create_engine(_db_uri, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def db_log_history(user_id, game_alias, platform, event_name, status_code, response_text, 
                   request_headers=None, request_body="", response_headers=None, response_time_ms=0.0):
    session = SessionLocal()
    try:
        new_entry = History(
            user_id=user_id,
            game_alias=game_alias,
            platform=platform,
            event_name=event_name,
            status_code=status_code,
            response_text=str(response_text) if response_text is not None else "",
            request_headers=request_headers or {},
            request_body=request_body or "",
            response_headers=response_headers or {},
            response_time_ms=response_time_ms
        )
        session.add(new_entry)
        session.commit()
    except Exception as e:
        print(f"Error logging history: {e}")
    finally:
        session.close()

def init_db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        if not session.query(User).filter(User.id == 0).first():
            session.add(User(id=0, balance=0.0, profile_data={"system": True}))
            session.commit()
    finally:
        session.close()

    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(games)")).fetchall()]
        if "device_os" not in cols:
            conn.execute(text("ALTER TABLE games ADD COLUMN device_os VARCHAR DEFAULT 'android'"))
        if "price" not in cols:
            conn.execute(text("ALTER TABLE games ADD COLUMN price FLOAT DEFAULT 0.0"))

    session = SessionLocal()
    try:
        games = session.query(Game).all()
        changed = False
        for g in games:
            current = (getattr(g, "device_os", None) or "").strip().lower()
            if not current:
                jd = dict(g.json_data or {})
                os_val = (jd.get("device_os") or "android").strip().lower()
                g.device_os = os_val
                jd["device_os"] = os_val
                g.json_data = jd
                changed = True
        if changed:
            session.commit()
    finally:
        session.close()

if __name__ == "__main__":
    init_db()
