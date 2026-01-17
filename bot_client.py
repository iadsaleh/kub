import os
import asyncio
import secrets
import json
import urllib.parse
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandObject, CommandStart, Command # إضافة CommandStart لدعم الروابط
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from sqlalchemy import desc

# استيراد المحرك، قاعدة البيانات، المجدول
from .s2s_engine import kun_engine
from .database import SessionLocal, User, History, Game, Coupon, GameTimeline, SystemSetting, db_log_history, ChatConversation, ChatMessage, Transaction, StoreCategory, StoreItem, StorePurchase
from .locales import TRANSLATIONS
# [ملاحظة هامة]: تم نقل استيراد admin_panel و educational إلى نهاية الملف لمنع المشاكل التقنية
from .scheduler import nexus_scheduler

# 1. إعدادات البيئة
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

def get_user_lang(user_id: int) -> str:
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user and user.profile_data:
            return user.profile_data.get("lang", "ar")
        return "ar"
    finally:
        session.close()

def t(key: str, lang: str = "ar", **kwargs) -> str:
    # Fallback to English if key missing in Lang
    # Fallback to Arabic if key missing in English (should not happen if synced)
    text = TRANSLATIONS.get(lang, {}).get(key)
    if not text:
        text = TRANSLATIONS.get("en", {}).get(key, key)
    
    if kwargs:
        try:
            return text.format(**kwargs)
        except:
            return text
    return text

# 2. تعريف حالات التدفق الاحترافي المحدثة (FSM)
class NexusFlow(StatesGroup):
    main_menu = State()
    searching_game = State()        
    selecting_search_os = State()
    selecting_timing = State()      
    waiting_for_levels = State()    
    waiting_for_sniper_lvl = State() 
    waiting_for_profile = State()   
    updating_gaid = State()          
    viewing_history = State()
    waiting_for_coupon = State() 
    waiting_for_custom_plan = State()
    adding_game_provider = State()
    adding_game_os = State()
    adding_game_name = State()
    adding_game_app_id = State()
    adding_game_main_key = State()
    adding_game_advanced = State()
    setting_proxy_base = State()
    setting_proxy_username = State()
    setting_proxy_password = State()
    selecting_language = State()
    chat_menu = State()
    chat_waiting_target = State()
    chat_waiting_message = State()

# 3. تعريف كائنات البوت والديسباتشر
bot = Bot(token=TOKEN)
dp = Dispatcher()

# جسر التواصل مع واجهة المدير (TUI)
log_queue = None
def set_log_queue(q: asyncio.Queue):
    global log_queue
    log_queue = q

async def send_log(msg: str):
    if log_queue is not None:
        try:
            log_queue.put_nowait(msg)
        except Exception: pass

def _slugify(value: str) -> str:
    raw = (value or "").strip().lower()
    out = []
    last_us = False
    for ch in raw:
        ok = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if ok:
            out.append(ch)
            last_us = False
        else:
            if not last_us:
                out.append("_")
                last_us = True
    alias = "".join(out).strip("_")
    return alias or "game"

def _ensure_unique_alias(session, base_alias: str, user_id: int) -> str:
    alias = base_alias
    if not session.query(Game).filter(Game.alias == alias).first():
        return alias
    alias = f"{base_alias}_{user_id}"
    if not session.query(Game).filter(Game.alias == alias).first():
        return alias
    suffix = secrets.token_hex(2)
    alias = f"{base_alias}_{user_id}_{suffix}"
    return alias

def _mask_proxy_url(proxy_url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(proxy_url)
        host = parts.hostname or ""
        port = parts.port
        scheme = parts.scheme or "http"
        auth = "auth" if parts.username else "noauth"
        if port:
            return f"{scheme}://{host}:{port} ({auth})"
        return f"{scheme}://{host} ({auth})"
    except Exception:
        return "invalid"

def _default_ua(device_os: str) -> str:
    os_name = (device_os or "android").strip().lower()
    if os_name == "ios":
        return "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    return "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)"

def _build_proxy_url(base: str, username: str | None, password: str | None) -> str:
    base = (base or "").strip()
    if not base:
        return ""
    if not (base.startswith("http://") or base.startswith("https://") or base.startswith("socks4://") or base.startswith("socks5://")):
        return ""
    if not username:
        return base
    u = urllib.parse.quote(username, safe="")
    p = urllib.parse.quote(password or "", safe="")
    parts = urllib.parse.urlsplit(base)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{u}:{p}@{host}{port}{path}{query}"

def _is_proxy_healthy(value: dict, proxy_url: str) -> bool:
    health = value.get("health") or {}
    info = health.get(proxy_url)
    if info is None:
        return True
    return bool(info.get("alive"))

def _pick_global_proxy_for_user(user_id: int) -> str:
    session = SessionLocal()
    try:
        setting = session.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
        value = setting.value if setting and isinstance(setting.value, dict) else {}
        proxies = [str(p).strip() for p in (value.get("proxies") or []) if str(p).strip()]
        if not setting or not proxies:
            return ""
        if len(proxies) == 1:
            return proxies[0] if _is_proxy_healthy(value, proxies[0]) else ""

        rotation = value.get("rotation") or {}
        last_user_id = rotation.get("last_user_id")
        current_index = int(rotation.get("current_index") or 0)
        next_index = int(rotation.get("next_index") or 0)

        n = len(proxies)
        chosen_index = None

        if last_user_id != user_id:
            idx = next_index % n
            for _ in range(n):
                if _is_proxy_healthy(value, proxies[idx]):
                    chosen_index = idx
                    break
                idx = (idx + 1) % n
        else:
            if 0 <= current_index < n and _is_proxy_healthy(value, proxies[current_index]):
                chosen_index = current_index
            else:
                idx = next_index % n
                for _ in range(n):
                    if _is_proxy_healthy(value, proxies[idx]):
                        chosen_index = idx
                        break
                    idx = (idx + 1) % n

        if chosen_index is None:
            return ""

        current_index = chosen_index
        next_index = (chosen_index + 1) % n
        value["rotation"] = {"last_user_id": user_id, "current_index": current_index, "next_index": next_index}
        setting.value = value
        session.commit()

        return proxies[current_index]
    finally:
        session.close()

def _apply_global_proxy(profile: dict, user_id: int) -> dict:
    if profile.get("proxy_url"):
        return profile
    proxy_url = _pick_global_proxy_for_user(int(user_id))
    if proxy_url:
        profile["proxy_url"] = proxy_url
        profile["proxy_display"] = _mask_proxy_url(proxy_url)
    return profile

def _get_or_create_user_admin_conversation(session, user_id: int) -> ChatConversation:
    conv = session.query(ChatConversation).filter(
        ChatConversation.kind == "user_admin",
        ChatConversation.user_a_id == user_id,
        ChatConversation.user_b_id == None,
        ChatConversation.is_closed == False
    ).first()
    if conv:
        return conv
    conv = ChatConversation(kind="user_admin", user_a_id=user_id, user_b_id=None)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv

# --- دالة إعداد أوامر البوت ---
async def setup_bot_commands(bot: Bot):
    commands = [
        types.BotCommand(command="start", description="🚀 القائمة الرئيسية"),
        types.BotCommand(command="admin", description="🛠️ لوحة تحكم المدير"),
        types.BotCommand(command="about", description="ℹ️ عن البوت"),
        types.BotCommand(command="help", description="📚 الدليل التعليمي")
    ]
    await bot.set_my_commands(commands)

# --- دالات النظام المالي (Finance Logic) ---
def get_current_op_price():
    try:
        if os.path.exists("op_price.txt"):
            with open("op_price.txt", "r") as f:
                return float(f.read().strip())
        return 0.05
    except: return 0.05

# --- دالات التعامل مع قاعدة البيانات (DB Wrappers) ---
def db_get_user(tg_id):
    session = SessionLocal()
    user = session.query(User).filter(User.id == tg_id).first()
    if not user:
        user = User(id=tg_id, balance=0.0, profile_data={"saved_gaid": "لم يتم الضبط ❌", "saved_idfa": "لم يتم الضبط ❌", "total_ops": 0})
        session.add(user)
        session.commit()
        session.refresh(user)
    session.close()
    return user

def db_update_user_profile(tg_id, new_gaid=None, inc_ops=False, charge_amount=0.0, global_chat_opt_in=None):
    session = SessionLocal()
    user = session.query(User).filter(User.id == tg_id).first()
    if user:
        p_data = dict(user.profile_data)
        if new_gaid: p_data["saved_gaid"] = new_gaid
        if inc_ops: p_data["total_ops"] = p_data.get("total_ops", 0) + 1
        if global_chat_opt_in is True:
            p_data["global_chat_opt_in"] = True
        user.profile_data = p_data
        if charge_amount != 0.0:
            user.balance -= charge_amount
            # تسجيل العملية المالية
            try:
                trans_type = "WITHDRAWAL" if charge_amount > 0 else "DEPOSIT"
                trans = Transaction(
                    user_id=tg_id,
                    amount=-charge_amount,
                    type=trans_type,
                    source="GAME_COST",
                    description="Service/Operation Cost" if charge_amount > 0 else "Refund/Adjustment"
                )
                session.add(trans)
            except Exception as e:
                print(f"Error logging transaction: {e}")
        session.commit()
    session.close()

# --- دالة الملاحة والتحكم ---
def add_nav_buttons(builder: InlineKeyboardBuilder, lang: str = "ar"):
    builder.row(
        types.InlineKeyboardButton(text=t("back_btn", lang), callback_data="back_to_prev"),
        types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main")
    )

def get_back_to_main_kb(lang: str = "ar"):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    return builder.as_markup()

def get_main_menu_kb(lang: str = "ar", user_id: int | None = None):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("game_list_btn", lang), callback_data="start_attack"))
    builder.row(types.InlineKeyboardButton(text="🛒 المتجر", callback_data="store_menu"))
    builder.row(types.InlineKeyboardButton(text=t("my_profile_btn", lang), callback_data="my_profile"))
    builder.row(types.InlineKeyboardButton(text=t("chat_btn", lang), callback_data="chat_menu"))
    builder.row(types.InlineKeyboardButton(text=t("edu_logs_btn", lang), callback_data="edu_main"))
    builder.row(types.InlineKeyboardButton(text=t("change_lang_btn", lang), callback_data="lang_menu"))
    admin_id = int(str(os.getenv("ADMIN_ID", "0")).strip() or "0")
    if user_id is not None and (admin_id == 0 or int(user_id) == admin_id):
        builder.row(types.InlineKeyboardButton(text=t("admin_panel_btn", lang), callback_data="admin:open_panel"))
    builder.row(types.InlineKeyboardButton(text=t("restart_btn", lang), callback_data="reset_bot"))
    return builder.as_markup()

def get_language_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="العربية", callback_data="set_lang:ar"))
    builder.row(types.InlineKeyboardButton(text="English", callback_data="set_lang:en"))
    builder.row(types.InlineKeyboardButton(text="Español", callback_data="set_lang:es"))
    builder.row(types.InlineKeyboardButton(text="हिन्दी", callback_data="set_lang:hi"))
    builder.row(types.InlineKeyboardButton(text="বাংলা", callback_data="set_lang:bn"))
    builder.row(types.InlineKeyboardButton(text="Français", callback_data="set_lang:fr"))
    builder.row(types.InlineKeyboardButton(text="Italiano", callback_data="set_lang:it"))
    builder.row(types.InlineKeyboardButton(text="Türkçe", callback_data="set_lang:tr"))
    return builder.as_markup()

@dp.callback_query(F.data == "lang_menu")
async def open_language_menu(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await callback.message.edit_text(t("select_lang", lang), reply_markup=get_language_kb())
    await callback.answer()

def get_timing_kb(lang: str = "ar"):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("smart_ai_btn", lang), callback_data="time:smart"))
    builder.row(types.InlineKeyboardButton(text=t("fast_btn", lang), callback_data="time:fast"))
    builder.row(types.InlineKeyboardButton(text=t("turbo_btn", lang), callback_data="time:turbo"))
    add_nav_buttons(builder, lang)
    return builder.as_markup()

# --- المعالجات الأساسية (Start Command & Deep Linking) ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext, command: CommandObject):
    user = db_get_user(message.from_user.id)
    p_data = user.profile_data or {}
    lang = p_data.get("lang", "ar")
    
    await state.set_state(NexusFlow.main_menu)
    
    # 🆕 معالجة الرابط السحري (Deep Linking) لربط الأجهزة
    args = command.args
    if args and args.startswith("LINK_DEVICE_"):
        try:
            # المتوقع: LINK_DEVICE_AndroidID__ModelName
            payload = args.replace("LINK_DEVICE_", "")
            
            if "__" in payload:
                android_id, model_safe = payload.split("__", 1)
                model = model_safe.replace("_", " ")
            else:
                android_id = payload
                model = "Unknown Device"

            # تحديث البروفايل في قاعدة البيانات
            session = SessionLocal()
            db_user = session.query(User).filter(User.id == message.from_user.id).first()
            if db_user:
                p_data = dict(db_user.profile_data or {})
                p_data['android_id'] = android_id
                p_data['device_model'] = model
                db_user.profile_data = p_data
                session.commit()
            session.close()

            await message.answer(
                t("device_linked", lang, model=model, android_id=android_id),
                reply_markup=get_main_menu_kb(lang, callback.from_user.id)
            )
            return # إنهاء الدالة هنا
            
        except Exception as e:
            await message.answer(t("link_error", lang, e=e))

    if not p_data.get("lang"):
        await message.answer(
            t("select_lang", "ar"), # Default prompt in Arabic (or could be bi-lingual)
            reply_markup=get_language_kb()
        )
        return

    await message.answer(t("welcome", lang), reply_markup=get_main_menu_kb(lang, message.from_user.id))

@dp.callback_query(F.data == "reset_bot")
async def reset_logic(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    lang = get_user_lang(callback.from_user.id)
    await callback.message.edit_text(t("welcome", lang), reply_markup=get_main_menu_kb(lang, callback.from_user.id))
    await callback.answer(t("system_reset", lang))

@dp.message(Command("about"))
async def cmd_about(message: types.Message):
    lang = get_user_lang(message.from_user.id)
    await message.answer(t("about_text", lang), parse_mode=None)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    from .educational import get_edu_menu_kb
    lang = get_user_lang(message.from_user.id)
    await message.answer(t("edu_intro", lang), reply_markup=get_edu_menu_kb())

# --- البروفايل ونظام الكوبونات ---
@dp.callback_query(F.data == "my_profile")
async def show_profile(callback: types.CallbackQuery, state: FSMContext):
    user = db_get_user(callback.from_user.id)
    p_data = user.profile_data
    lang = p_data.get("lang", "ar")
    
    proxy_base = (p_data.get("proxy_base") or "").strip()
    proxy_username = (p_data.get("proxy_username") or "").strip()
    proxy_password = (p_data.get("proxy_password") or "").strip()
    proxy_url = _build_proxy_url(proxy_base, proxy_username or None, proxy_password or None) if proxy_base else ""
    proxy_display = _mask_proxy_url(proxy_url) if proxy_url else "---"
    
    profile_text = (
        f"{t('profile_header', lang)}\n━━━━━━━━━━━━━━━\n"
        f"🆔 **ID:** `{user.id}`\n{t('rank', lang)} `VIP Developer`\n"
        f"{t('balance', lang)} `${user.balance:.2f}`\n\n"
        f"{t('saved_gaid', lang)}\n`{p_data.get('saved_gaid')}`\n"
        f"{t('saved_idfa', lang)}\n`{p_data.get('saved_idfa')}`\n"
        f"{t('android_id', lang)}\n`{p_data.get('android_id', '---')}`\n\n"
        f"{t('proxy', lang)} `{proxy_display}`\n\n"
        f"{t('total_ops', lang)} `{p_data.get('total_ops')}`\n━━━━━━━━━━━━━━━"
    )
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("update_gaid_btn", lang), callback_data="update_gaid_manual"))
    builder.row(types.InlineKeyboardButton(text=t("setup_proxy_btn", lang), callback_data="proxy_setup"))
    if proxy_base:
        builder.row(types.InlineKeyboardButton(text=t("remove_proxy_btn", lang), callback_data="proxy_remove"))
    builder.row(types.InlineKeyboardButton(text=t("use_coupon_btn", lang), callback_data="use_coupon"))
    builder.row(types.InlineKeyboardButton(text=t("add_private_game_btn", lang), callback_data="add_private_game"))
    builder.row(types.InlineKeyboardButton(text=t("my_private_games_btn", lang), callback_data="my_private_games"))
    builder.row(types.InlineKeyboardButton(text="🧾 مشترياتي", callback_data="my_purchases"))
    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    await callback.message.edit_text(profile_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

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

def _ensure_default_store_categories(session: SessionLocal):
    if session.query(StoreCategory).count() > 0:
        return
    defaults = [
        ("سكربتات فريدا", "frida_scripts", 10),
        ("سكربتات غيم غارديان", "gameguardian_scripts", 20),
        ("دروس تهكير الألعاب باستخدام الحقن", "injection_tutorials", 30),
        ("دروس جمبرة", "jailbreak_tutorials", 40),
    ]
    for name, slug, order in defaults:
        session.add(StoreCategory(name=name, slug=slug, sort_order=order, is_active=True))
    session.commit()

def _get_store_channel_ids() -> list[int]:
    env_many = (os.getenv("STORE_CHANNEL_IDS") or "").strip()
    if env_many:
        out = []
        for part in env_many.split(","):
            s = part.strip()
            if not s:
                continue
            try:
                out.append(int(s))
            except Exception:
                continue
        return list(dict.fromkeys(out))

    env_one = (os.getenv("STORE_CHANNEL_ID") or "").strip()
    if env_one:
        try:
            return [int(env_one)]
        except Exception:
            return []

    session = SessionLocal()
    try:
        direct = session.query(SystemSetting).filter(SystemSetting.key == "store_channel_ids").first()
        if direct and isinstance(direct.value, list):
            out = []
            for c in direct.value:
                try:
                    out.append(int(c))
                except Exception:
                    continue
            return list(dict.fromkeys(out))

        setting = session.query(SystemSetting).filter(SystemSetting.key == "store_channels").first()
        if setting:
            value = setting.value
            channels = []
            if isinstance(value, dict):
                channels = value.get("channels") or []
            elif isinstance(value, list):
                channels = value
            out = []
            for c in channels:
                try:
                    out.append(int(c))
                except Exception:
                    continue
            return list(dict.fromkeys(out))

        legacy = session.query(SystemSetting).filter(SystemSetting.key == "store_channel_id").first()
        if legacy:
            try:
                return [int(legacy.value)]
            except Exception:
                return []
        return []
    finally:
        session.close()

async def _ingest_store_message(message: types.Message):
    channel_ids = _get_store_channel_ids()
    if not channel_ids:
        return

    origin_chat_id = message.chat.id
    origin_message_id = message.message_id

    fchat = getattr(message, "forward_from_chat", None)
    fmid = getattr(message, "forward_from_message_id", None)
    if fchat is not None and fmid is not None:
        try:
            origin_chat_id = int(getattr(fchat, "id"))
            origin_message_id = int(fmid)
        except Exception:
            origin_chat_id = message.chat.id
            origin_message_id = message.message_id

    in_list = origin_chat_id in channel_ids
    if message.chat.type == "channel" or fchat is not None:
        await send_log(f"[store] update chat={origin_chat_id} in_list={in_list} msg={origin_message_id}")
    if not in_list:
        return

    file_type = None
    file_id = None
    file_unique_id = None
    file_name = None

    if message.document:
        file_type = "document"
        file_id = message.document.file_id
        file_unique_id = message.document.file_unique_id
        file_name = message.document.file_name
    elif message.video:
        file_type = "video"
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
        file_name = message.video.file_name
    elif message.audio:
        file_type = "audio"
        file_id = message.audio.file_id
        file_unique_id = message.audio.file_unique_id
        file_name = message.audio.file_name
    elif message.voice:
        file_type = "voice"
        file_id = message.voice.file_id
        file_unique_id = message.voice.file_unique_id
        file_name = None
    elif message.photo:
        file_type = "photo"
        ph = message.photo[-1]
        file_id = ph.file_id
        file_unique_id = ph.file_unique_id
        file_name = None
    else:
        return

    await send_log(f"[store] ingest chat={origin_chat_id} msg={origin_message_id} type={file_type}")

    title = (message.caption or "").strip() or (file_name or "").strip() or f"Item {origin_message_id}"
    description = (message.caption or "").strip() or None

    session = SessionLocal()
    try:
        item = StoreItem(
            category_id=None,
            title=title[:200],
            description=description,
            price=0.0,
            is_active=False,
            source_chat_id=origin_chat_id,
            source_message_id=origin_message_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            file_name=file_name,
            meta={},
        )
        session.add(item)
        session.commit()
        await send_log(f"[store] saved item chat={origin_chat_id} msg={origin_message_id}")
    except Exception:
        session.rollback()
        await send_log(f"[store] failed save chat={origin_chat_id} msg={origin_message_id}")
    finally:
        session.close()

@dp.channel_post()
async def store_channel_ingest_channel_post(message: types.Message):
    await _ingest_store_message(message)

@dp.message(F.chat.type == "channel")
async def store_channel_ingest_message(message: types.Message):
    await _ingest_store_message(message)

@dp.message(F.forward_from_chat)
async def store_channel_ingest_forwarded(message: types.Message):
    await _ingest_store_message(message)

async def _send_store_item_file(bot: Bot, chat_id: int, item: StoreItem):
    ft = (item.file_type or "").lower()
    caption = (item.title or "").strip()
    if ft == "document":
        await bot.send_document(chat_id, item.file_id, caption=caption)
    elif ft == "video":
        await bot.send_video(chat_id, item.file_id, caption=caption)
    elif ft == "audio":
        await bot.send_audio(chat_id, item.file_id, caption=caption)
    elif ft == "voice":
        await bot.send_voice(chat_id, item.file_id, caption=caption)
    elif ft == "photo":
        await bot.send_photo(chat_id, item.file_id, caption=caption)
    else:
        await bot.send_message(chat_id, caption)

@dp.callback_query(F.data == "store_menu")
async def open_store_menu(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        cats = session.query(StoreCategory).filter(StoreCategory.is_active == True).order_by(StoreCategory.sort_order.asc(), StoreCategory.id.asc()).all()
        builder = InlineKeyboardBuilder()
        for c in cats:
            count = session.query(StoreItem).filter(StoreItem.category_id == c.id, StoreItem.is_active == True).count()
            builder.row(types.InlineKeyboardButton(text=f"📦 {c.name} ({count})", callback_data=f"store_cat:{c.id}:0"))
        add_nav_buttons(builder, lang)
        await callback.message.edit_text("🛒 المتجر\nاختر القسم:", reply_markup=builder.as_markup())
        await callback.answer()
    finally:
        session.close()

@dp.callback_query(F.data.startswith("store_cat:"))
async def open_store_category(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    parts = callback.data.split(":")
    try:
        cat_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer()
        return

    page = max(0, page)
    page_size = 10
    session = SessionLocal()
    try:
        cat = session.query(StoreCategory).filter(StoreCategory.id == cat_id, StoreCategory.is_active == True).first()
        if not cat:
            await callback.answer("القسم غير موجود", show_alert=True)
            return
        q = session.query(StoreItem).filter(StoreItem.category_id == cat_id, StoreItem.is_active == True).order_by(StoreItem.created_at.desc())
        total = q.count()
        items = q.offset(page * page_size).limit(page_size).all()

        builder = InlineKeyboardBuilder()
        for it in items:
            price = float(it.price or 0.0)
            builder.row(types.InlineKeyboardButton(text=f"{it.title} - ${price:.2f}", callback_data=f"store_item:{it.id}:{cat_id}:{page}"))
        if total > (page + 1) * page_size:
            builder.row(types.InlineKeyboardButton(text="التالي ➡️", callback_data=f"store_cat:{cat_id}:{page+1}"))
        if page > 0:
            builder.row(types.InlineKeyboardButton(text="⬅️ السابق", callback_data=f"store_cat:{cat_id}:{page-1}"))
        add_nav_buttons(builder, lang)
        await callback.message.edit_text(f"🛒 {cat.name}\nاختر المحتوى:", reply_markup=builder.as_markup())
        await callback.answer()
    finally:
        session.close()

@dp.callback_query(F.data.startswith("store_item:"))
async def open_store_item(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    parts = callback.data.split(":")
    try:
        item_id = int(parts[1])
        cat_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await callback.answer()
        return

    session = SessionLocal()
    try:
        item = session.query(StoreItem).filter(StoreItem.id == item_id, StoreItem.is_active == True).first()
        if not item:
            await callback.answer("المحتوى غير موجود", show_alert=True)
            return
        purchased = session.query(StorePurchase).filter(StorePurchase.user_id == callback.from_user.id, StorePurchase.item_id == item_id).first() is not None
        price = float(item.price or 0.0)
        desc_text = (item.description or "").strip()
        text = f"🧾 {item.title}\n💵 السعر: ${price:.2f}"
        if desc_text:
            text += f"\n\n{desc_text}"
        builder = InlineKeyboardBuilder()
        if purchased:
            builder.row(types.InlineKeyboardButton(text="⬇️ تحميل", callback_data=f"store_dl:{item_id}:{cat_id}:{page}"))
        else:
            builder.row(types.InlineKeyboardButton(text="🛒 شراء", callback_data=f"store_buy:{item_id}:{cat_id}:{page}"))
        builder.row(types.InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"store_cat:{cat_id}:{page}"))
        builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()
    finally:
        session.close()

@dp.callback_query(F.data.startswith("store_buy:"))
async def buy_store_item(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    try:
        item_id = int(parts[1])
        cat_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await callback.answer()
        return

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == callback.from_user.id).first()
        item = session.query(StoreItem).filter(StoreItem.id == item_id, StoreItem.is_active == True).first()
        if not user or not item:
            await callback.answer("غير متاح", show_alert=True)
            return
        existing = session.query(StorePurchase).filter(StorePurchase.user_id == user.id, StorePurchase.item_id == item_id).first()
        if existing:
            await callback.answer("تم الشراء مسبقاً", show_alert=True)
            return

        price = float(item.price or 0.0)
        if price <= 0:
            await callback.answer("المنتج غير مسعّر بعد", show_alert=True)
            return
        if float(user.balance or 0.0) < price:
            await callback.answer("رصيدك غير كافٍ", show_alert=True)
            return

        user.balance = float(user.balance or 0.0) - price
        session.add(StorePurchase(user_id=user.id, item_id=item.id, price_paid=price))
        session.add(Transaction(user_id=user.id, amount=-price, type="WITHDRAWAL", source="STORE", description=f"Store purchase: {item.title}"))
        session.commit()
    except Exception:
        session.rollback()
        await callback.answer("فشل الشراء", show_alert=True)
        return
    finally:
        session.close()

    await callback.answer("تم الشراء ✅", show_alert=True)
    await open_store_item(callback, state)

@dp.callback_query(F.data.startswith("store_dl:"))
async def download_store_item(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    try:
        item_id = int(parts[1])
        cat_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await callback.answer()
        return

    session = SessionLocal()
    try:
        item = session.query(StoreItem).filter(StoreItem.id == item_id).first()
        if not item:
            await callback.answer("غير موجود", show_alert=True)
            return
        purchased = session.query(StorePurchase).filter(StorePurchase.user_id == callback.from_user.id, StorePurchase.item_id == item_id).first()
        if not purchased:
            await callback.answer("غير مشتراة", show_alert=True)
            return
    finally:
        session.close()

    try:
        await _send_store_item_file(callback.message.bot, callback.from_user.id, item)
        await callback.answer("تم الإرسال ✅")
    except Exception:
        await callback.answer("تعذر إرسال الملف", show_alert=True)

@dp.callback_query(F.data == "my_purchases")
async def show_my_purchases(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    session = SessionLocal()
    try:
        rows = (
            session.query(StorePurchase)
            .filter(StorePurchase.user_id == callback.from_user.id)
            .order_by(StorePurchase.created_at.desc())
            .limit(30)
            .all()
        )
        builder = InlineKeyboardBuilder()
        if not rows:
            builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
            await callback.message.edit_text("🧾 مشترياتي\nلا يوجد مشتريات بعد.", reply_markup=builder.as_markup())
            await callback.answer()
            return

        item_ids = [r.item_id for r in rows]
        items = session.query(StoreItem).filter(StoreItem.id.in_(item_ids)).all()
        items_by_id = {i.id: i for i in items}
        for r in rows:
            it = items_by_id.get(r.item_id)
            if not it:
                continue
            builder.row(types.InlineKeyboardButton(text=f"⬇️ {it.title}", callback_data=f"store_dl:{it.id}:0:0"))
        builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
        await callback.message.edit_text("🧾 مشترياتي\nاختر لتحميل:", reply_markup=builder.as_markup())
        await callback.answer()
    finally:
        session.close()

@dp.callback_query(F.data == "proxy_remove")
async def remove_proxy(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == callback.from_user.id).first()
        if user:
            p = dict(user.profile_data or {})
            p.pop("proxy_base", None)
            p.pop("proxy_username", None)
            p.pop("proxy_password", None)
            user.profile_data = p
            session.commit()
    finally:
        session.close()
    await callback.answer(t("proxy_removed", lang))
    await show_profile(callback, state)

@dp.callback_query(F.data == "proxy_setup")
async def start_proxy_setup(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await state.set_state(NexusFlow.setting_proxy_base)
    await callback.message.answer(
        t("proxy_setup_intro", lang),
        parse_mode="Markdown"
    )

@dp.message(NexusFlow.setting_proxy_base)
async def set_proxy_base(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    raw = (message.text or "").strip()
    if raw.lower() in ["off", "remove", "delete"]:
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == message.from_user.id).first()
            if user:
                p = dict(user.profile_data or {})
                p.pop("proxy_base", None)
                p.pop("proxy_username", None)
                p.pop("proxy_password", None)
                user.profile_data = p
                session.commit()
        finally:
            session.close()
        await message.answer(t("proxy_removed", lang), reply_markup=get_back_to_main_kb(lang))
        await state.set_state(NexusFlow.main_menu)
        return

    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks4://") or raw.startswith("socks5://")):
        await message.answer(t("proxy_format_error", lang))
        return

    # 🆕 التحقق الفوري من البروكسي
    status_msg = await message.answer(t("checking_proxy", lang))
    
    # محاولة استخراج اليوزر/الباس إذا كان موجوداً في الرابط
    # إذا كان الرابط كاملاً مثل http://user:pass@ip:port، سيتم حفظه كما هو في proxy_base
    # ولكن نحتاج للتأكد من أنه يعمل
    
    success, ip, details = await kun_engine.check_proxy(raw)
    
    if success:
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == message.from_user.id).first()
            if user:
                p = dict(user.profile_data or {})
                p["proxy_base"] = raw
                p["proxy_username"] = "" # Reset legacy fields if full URL used
                p["proxy_password"] = ""
                user.profile_data = p
                session.commit()
        finally:
            session.close()
            
        await status_msg.edit_text(
            t("proxy_success", lang, ip=ip, country=details.get("country", "Unknown")),
            reply_markup=get_back_to_main_kb(lang)
        )
        await state.set_state(NexusFlow.main_menu)
    else:
        await status_msg.edit_text(
            t("proxy_fail", lang, error=ip), # ip here is the error message
            reply_markup=get_back_to_main_kb(lang)
        )

    # Removed legacy multi-step proxy setup
    return

@dp.callback_query(F.data.startswith("set_lang:"))
async def set_language(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    allowed = {"ar", "en", "es", "hi", "bn", "fr", "it", "tr"}
    if code not in allowed:
        await callback.answer(t("unsupported_lang", "ar"), show_alert=True)
        return
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == callback.from_user.id).first()
        if user:
            p = dict(user.profile_data or {})
            p["lang"] = code
            user.profile_data = p
            session.commit()
    finally:
        session.close()
    await callback.message.edit_text(t("lang_saved", code), reply_markup=get_main_menu_kb(code, callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "use_coupon")
async def coupon_input_start(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await state.set_state(NexusFlow.waiting_for_coupon)
    await callback.message.answer(t("coupon_prompt", lang))

@dp.message(NexusFlow.waiting_for_coupon)
async def process_coupon_redemption(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    code = message.text.strip()
    session = SessionLocal()
    coupon = session.query(Coupon).filter(Coupon.code == code, Coupon.is_used == False).first()
    if coupon:
        user = session.query(User).filter(User.id == message.from_user.id).first()
        user.balance += coupon.amount
        coupon.is_used = True
        coupon.used_by = message.from_user.id
        
        # Log Transaction
        trans = Transaction(
            user_id=message.from_user.id,
            amount=coupon.amount,
            type="DEPOSIT",
            source="COUPON",
            description=f"Redeemed Coupon: {code}"
        )
        session.add(trans)
        
        session.commit()
        await message.answer(t("coupon_success", lang, amount=coupon.amount, balance=f"{user.balance:.2f}"), reply_markup=get_back_to_main_kb(lang))
        await state.set_state(NexusFlow.main_menu)
    else:
        await message.answer(t("coupon_invalid", lang))
    session.close()

# --- منطق البحث والأحداث (محدث لاستخدام قاعدة البيانات) ---
@dp.callback_query(F.data == "start_attack")
async def start_search_logic(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🤖 Android", callback_data="search_os:android"))
    builder.row(types.InlineKeyboardButton(text="🍎 iPhone (iOS)", callback_data="search_os:ios"))
    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    await state.set_state(NexusFlow.selecting_search_os)
    await callback.message.edit_text(t("search_os_prompt", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("search_os:"))
async def pick_search_os(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    device_os = callback.data.split(":", 1)[1].strip().lower()
    await state.update_data(search_device_os=device_os)
    await state.set_state(NexusFlow.searching_game)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    await callback.message.edit_text(t("enter_game_search", lang), reply_markup=builder.as_markup())

@dp.message(NexusFlow.searching_game)
async def process_game_search(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    query = (message.text or "").strip()
    if not query:
        await message.answer(t("no_results", lang))
        return
    query_l = query.lower()
    user_id = message.from_user.id
    data = await state.get_data()
    device_os = (data.get("search_device_os") or "android").strip().lower()
    session = SessionLocal()
    candidates = session.query(Game).filter(
        (Game.owner_id == None) | (Game.owner_id == user_id)
    ).all()
    session.close()

    results = []
    for g in candidates:
        g_os = (getattr(g, "device_os", None) or (g.json_data or {}).get("device_os") or "android")
        g_os = str(g_os).strip().lower()
        if g_os != device_os:
            continue
        name_l = (g.name or "").strip().lower()
        alias_l = (g.alias or "").strip().lower()
        if query_l in name_l or query_l in alias_l:
            results.append(g)
            if len(results) >= 20:
                break
    
    if not results:
        await message.answer(t("no_results", lang))
        return
        
    builder = InlineKeyboardBuilder()
    for game_obj in results:
        display_name = game_obj.name or game_obj.alias
        builder.row(types.InlineKeyboardButton(text=f"✅ {display_name[:25]}", callback_data=f"game_select:{game_obj.alias}"))
    
    add_nav_buttons(builder, lang)
    await message.answer(t("search_results", lang, query=query), reply_markup=builder.as_markup())

@dp.callback_query(F.data == "add_private_game")
async def start_add_private_game(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await state.clear()
    await state.set_state(NexusFlow.adding_game_provider)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="AppsFlyer", callback_data="add_game_provider:AppsFlyer"))
    builder.row(types.InlineKeyboardButton(text="Adjust", callback_data="add_game_provider:Adjust"))
    builder.row(types.InlineKeyboardButton(text="Singular", callback_data="add_game_provider:Singular"))
    add_nav_buttons(builder, lang)
    await callback.message.edit_text(t("select_provider", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("add_game_provider:"))
async def pick_add_game_provider(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    provider = callback.data.split(":", 1)[1]
    await state.update_data(add_game_provider=provider)
    await state.set_state(NexusFlow.adding_game_os)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="Android", callback_data="add_game_os:android"))
    builder.row(types.InlineKeyboardButton(text="iPhone (iOS)", callback_data="add_game_os:ios"))
    add_nav_buttons(builder, lang)
    await callback.message.edit_text(t("select_game_os", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("add_game_os:"))
async def pick_add_game_os(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    device_os = callback.data.split(":", 1)[1]
    await state.update_data(add_game_os=device_os)
    await state.set_state(NexusFlow.adding_game_name)
    await callback.message.edit_text(t("enter_game_name", lang), reply_markup=get_back_to_main_kb(lang))

@dp.message(NexusFlow.adding_game_name)
async def add_game_name(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer(t("game_name_short", lang))
        return
    data = await state.get_data()
    provider = data.get("add_game_provider")
    await state.update_data(add_game_name=name)
    if provider in ["AppsFlyer", "Singular"]:
        await state.set_state(NexusFlow.adding_game_app_id)
        await message.answer(t("enter_app_id", lang))
    else:
        await state.set_state(NexusFlow.adding_game_main_key)
        await message.answer(t("enter_app_token", lang))

@dp.message(NexusFlow.adding_game_app_id)
async def add_game_app_id(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    app_id = (message.text or "").strip()
    if len(app_id) < 3:
        await message.answer(t("invalid_app_id", lang))
        return
    await state.update_data(add_game_app_id=app_id)
    data = await state.get_data()
    provider = data.get("add_game_provider")
    await state.set_state(NexusFlow.adding_game_main_key)
    if provider == "AppsFlyer":
        await message.answer(t("enter_dev_key", lang))
    else:
        await message.answer(t("enter_api_key", lang))

@dp.message(NexusFlow.adding_game_main_key)
async def add_game_main_key(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    key = (message.text or "").strip()
    data = await state.get_data()
    provider = data.get("add_game_provider")

    if provider == "Adjust":
        if len(key) != 12 or not key.isalnum():
            await message.answer(t("invalid_app_token", lang))
            return
    elif provider == "AppsFlyer":
        if len(key) < 5:
            await message.answer(t("dev_key_short", lang))
            return
    elif provider == "Singular":
        if len(key) < 20:
            await message.answer(t("api_key_short", lang))
            return

    await state.update_data(add_game_main_key=key)
    await state.set_state(NexusFlow.adding_game_advanced)

    if provider == "AppsFlyer":
        await message.answer(t("enter_advanced_af", lang))
    elif provider == "Adjust":
        await message.answer(t("enter_advanced_adjust", lang))
    else:
        await message.answer(t("enter_advanced_singular", lang))

@dp.message(NexusFlow.adding_game_advanced)
async def add_game_advanced(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    advanced = (message.text or "").strip()
    user_id = message.from_user.id

    data = await state.get_data()
    provider = data.get("add_game_provider")
    name = data.get("add_game_name")
    app_id = data.get("add_game_app_id")
    key = data.get("add_game_main_key")
    device_os = (data.get("add_game_os") or "android").strip().lower()

    db_get_user(user_id)

    session = SessionLocal()
    try:
        base_alias = _slugify(name)
        alias = _ensure_unique_alias(session, base_alias, user_id)

        json_data = {"provider": provider, "device_os": device_os}
        pkg_name = None

        if provider == "AppsFlyer":
            pkg_name = app_id
            json_data["app_id"] = app_id
            json_data["dev_key"] = key
            template_str = "{\"af_level\":\"{LEVEL}\",\"af_score\":100}" if advanced.lower() == "default" else advanced
            json_data["event_templates"] = {
                "level_up": {"event_name": "af_level_achieved", "json_template": template_str}
            }
        elif provider == "Adjust":
            json_data["app_token"] = key
            json_data["environment"] = "production"
            json_data["level_sequence"] = []
            if advanced.lower() != "skip":
                for line in advanced.split("\n"):
                    if ":" in line:
                        parts = line.split(":", 1)
                        lvl = parts[0].strip()
                        tkn = parts[1].strip()
                        if lvl and tkn:
                            json_data["level_sequence"].append({"lvl": lvl, "tkn": tkn})
        else:
            pkg_name = app_id
            json_data["app_id"] = app_id
            json_data["api_key"] = key
            if advanced.lower() != "skip":
                json_data["secret"] = advanced

        new_game = Game(
            alias=alias,
            name=name,
            package_name=pkg_name,
            device_os=device_os,
            provider=provider,
            json_data=json_data,
            is_active=True,
            owner_id=user_id,
        )
        session.add(new_game)
        session.commit()

    finally:
        session.close()

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("use_game_btn", lang), callback_data=f"game_select:{alias}"))
    builder.row(types.InlineKeyboardButton(text=t("my_private_games_btn", lang), callback_data="my_private_games"))
    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    await message.answer(t("private_game_added", lang, alias=alias), reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.set_state(NexusFlow.main_menu)

@dp.callback_query(F.data == "my_private_games")
async def show_my_private_games(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    session = SessionLocal()
    try:
        games = (
            session.query(Game)
            .filter(Game.owner_id == callback.from_user.id)
            .order_by(Game.name.asc())
            .limit(50)
            .all()
        )
    finally:
        session.close()

    builder = InlineKeyboardBuilder()
    if not games:
        builder.row(types.InlineKeyboardButton(text=t("add_private_game_btn", lang), callback_data="add_private_game"))
        add_nav_buttons(builder, lang)
        await callback.message.edit_text(t("no_private_games", lang), reply_markup=builder.as_markup())
        return

    for g in games:
        display_name = g.name or g.alias
        builder.row(types.InlineKeyboardButton(text=f"🔒 {display_name[:25]}", callback_data=f"game_select:{g.alias}"))
    add_nav_buttons(builder, lang)
    await callback.message.edit_text(t("your_private_games", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("game_select:"))
async def handle_game_selection(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    game_key = callback.data.split(":")[1]
    
    session = SessionLocal()
    game_obj = session.query(Game).filter(Game.alias == game_key).first()
    session.close()
    
    if not game_obj:
        await callback.answer(t("game_not_found_db", lang), show_alert=True)
        return

    if game_obj.owner_id and game_obj.owner_id != callback.from_user.id:
        await callback.answer(t("game_no_access", lang), show_alert=True)
        return

    game_data = game_obj.json_data or {}
    provider = game_obj.provider or game_data.get("provider")
    
    if not provider:
        if "app_token" in game_data: provider = "Adjust"
        elif "dev_key" in game_data: provider = "AppsFlyer"
        else: provider = "Unknown"
    
    platform = provider.lower()
    
    # التحقق من تفعيل المنصة في إعدادات النظام
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    client_enabled = True
    server_enabled = True
    
    if sys_setting and sys_setting.value:
        client_enabled = sys_setting.value.get(f"{platform}_client", True)
        server_enabled = sys_setting.value.get(f"{platform}_server", True)
        
    if not client_enabled and not server_enabled:
        await callback.answer(t("platform_maintenance", lang, platform=platform.title()), show_alert=True)
        return

    device_os = (getattr(game_obj, "device_os", None) or game_data.get("device_os") or "android").strip().lower()
    await state.update_data(selected_game=game_key, platform=platform, device_os=device_os)
    game_display_name = game_obj.name
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🎯 Sniper Mode", callback_data="mode_sniper"))
    builder.row(types.InlineKeyboardButton(text="🚜 Farm Mode", callback_data="mode_farm"))
    builder.row(types.InlineKeyboardButton(text="🎭 Natural Path (رسمي)", callback_data="mode_natural"))
    builder.row(types.InlineKeyboardButton(text="✍️ Custom Plan (خاص)", callback_data="mode_custom_plan"))
    add_nav_buttons(builder, lang)
    
    await callback.message.edit_text(
        f"🎯 اللعبة: `{game_display_name}`\n"
        f"📡 المنصة: `{platform.upper()}`\n"
        f"{t('select_send_mode', lang)}", 
        reply_markup=builder.as_markup()
    )

# --- معالجة الأوضاع المتقدمة (Natural & Custom) ---
@dp.callback_query(F.data == "mode_natural")
async def start_natural_mode(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    data = await state.get_data()
    game_key = data.get("selected_game")
    platform = data.get("platform")
    device_os = (data.get("device_os") or "android").upper()
    
    # Check Server Setting
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    if sys_setting and sys_setting.value:
         if not sys_setting.value.get(f"{platform}_server", True):
              await callback.answer(t("server_maintenance", lang, platform=platform.title()), show_alert=True)
              return

    session = SessionLocal()
    game = session.query(Game).filter(Game.alias == game_key).first()
    timelines = session.query(GameTimeline).filter(GameTimeline.game_id == game.id).all() if game else []
    session.close()

    if not timelines:
        await callback.answer(t("no_natural_plan", lang))
        return

    await state.update_data(exec_mode="natural", timelines_count=len(timelines))
    id_label = "af_id" if platform == "appsflyer" else "ad_id"
    ad_label = "GAID" if device_os == "ANDROID" else "IDFA"
    third_label = "AID" if device_os == "ANDROID" else "IDFV"
    await callback.message.answer(t("natural_mode_intro", lang, count=len(timelines), ids=f"{ad_label}|{id_label}|{third_label}|UA"))
    await state.set_state(NexusFlow.waiting_for_profile)

@dp.callback_query(F.data == "mode_custom_plan")
async def start_custom_plan_mode(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    data = await state.get_data()
    platform = data.get("platform")
    
    # Check Server Setting
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    if sys_setting and sys_setting.value:
         if not sys_setting.value.get(f"{platform}_server", True):
              await callback.answer(t("server_maintenance", lang, platform=platform.title()), show_alert=True)
              return

    await state.update_data(exec_mode="custom")
    await callback.message.answer(t("custom_plan_intro", lang), parse_mode="Markdown")
    await state.set_state(NexusFlow.waiting_for_custom_plan)

@dp.message(NexusFlow.waiting_for_custom_plan)
async def process_custom_plan_input(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    lines = message.text.strip().split('\n')
    steps = []
    try:
        for line in lines:
            if '|' not in line: continue
            p = line.split('|')
            steps.append({"step": p[0].strip(), "delay_hours": int(p[1].strip())})
        
        if not steps: raise ValueError()
        
        await state.update_data(custom_plan_steps=steps)
        data = await state.get_data()
        platform = data.get("platform", "unknown")
        device_os = (await state.get_data()).get("device_os", "android").upper()
        id_label = "af_id" if platform == "appsflyer" else "ad_id"
        ad_label = "GAID" if device_os == "ANDROID" else "IDFA"
        third_label = "AID" if device_os == "ANDROID" else "IDFV"
        await message.answer(t("custom_plan_received", lang, count=len(steps), ids=f"{ad_label}|{id_label}|{third_label}|UA"))
        await state.set_state(NexusFlow.waiting_for_profile)
    except:
        await message.answer(t("custom_plan_error", lang))

# --- الأوضاع التقليدية (Sniper & Farm) ---
@dp.callback_query(F.data == "mode_sniper")
async def handle_sniper_mode_init(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    data = await state.get_data()
    game_key = data.get("selected_game")
    
    session = SessionLocal()
    game_obj = session.query(Game).filter(Game.alias == game_key).first()
    session.close()
    
    if not game_obj:
        await callback.answer(t("game_not_found_generic", lang), show_alert=True)
        return

    game_data = game_obj.json_data or {}
    platform = data.get("platform")
    
    builder = InlineKeyboardBuilder()
    if platform == "adjust":
        for seq in game_data.get("level_sequence", []): 
            lvl_name = seq.get("lvl", "Unknown")
            builder.row(types.InlineKeyboardButton(text=f"📍 {lvl_name}", callback_data=f"evt_p:{seq['tkn']}:{lvl_name}"))
    else:
        for evt in game_data.get("event_templates", {}).keys():
            builder.row(types.InlineKeyboardButton(text=f"🔥 Sniper: {evt}", callback_data=f"evt_p:{evt}:0"))
    add_nav_buttons(builder, lang)
    await callback.message.edit_text(t("sniper_mode_intro", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data == "mode_farm")
async def start_farm_mode(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    data = await state.get_data()
    platform = data.get("platform")
    
    # Check Server Setting
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    if sys_setting and sys_setting.value:
         if not sys_setting.value.get(f"{platform}_server", True):
              await callback.answer(t("server_maintenance", lang, platform=platform.title()), show_alert=True)
              return

    await state.update_data(exec_mode="farm")
    if data.get("platform") == "appsflyer":
        await callback.message.edit_text(t("farm_mode_intro", lang))
        await state.set_state(NexusFlow.waiting_for_levels)
    else:
        await callback.message.edit_text(t("timing_strategy_intro", lang), reply_markup=get_timing_kb(lang))
        await state.set_state(NexusFlow.selecting_timing)

@dp.message(NexusFlow.waiting_for_levels)
async def process_custom_levels(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    try:
        levels = [int(l) for l in message.text.replace(",", " ").split()]
        await state.update_data(custom_levels=levels)
        await message.answer(t("timing_strategy_intro_2", lang), reply_markup=get_timing_kb(lang))
        await state.set_state(NexusFlow.selecting_timing)
    except: await message.answer(t("invalid_number", lang))

@dp.callback_query(F.data.startswith("time:"))
async def handle_timing_selection(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(timing_strategy=callback.data.split(":")[1])
    user = db_get_user(callback.from_user.id)
    lang = get_user_lang(callback.from_user.id)
    st = await state.get_data()
    platform = st.get("platform", "").upper()
    device_os = (st.get("device_os") or "android").upper()
    id_label = "af_id" if platform == "APPSFLYER" else "ad_id"
    ad_label = "GAID" if device_os == "ANDROID" else "IDFA"
    third_label = "AID" if device_os == "ANDROID" else "IDFV"
    saved_ad = user.profile_data.get("saved_gaid") if device_os == "ANDROID" else user.profile_data.get("saved_idfa")
    bound_third = user.profile_data.get("android_id") if device_os == "ANDROID" else user.profile_data.get("idfv")
    hint = t("id_requirement_hint", lang, platform=platform, ad_label=ad_label, id_label=id_label, third_label=third_label, saved_ad=saved_ad, bound_third=bound_third)
    await callback.message.answer(hint)
    await state.set_state(NexusFlow.waiting_for_profile)

@dp.callback_query(F.data.startswith("evt_p:"))
async def handle_sniper_selection(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    evt_val = parts[1]
    level_num = parts[2] if len(parts) > 2 else "0"
    lang = get_user_lang(callback.from_user.id)
    
    await state.update_data(evt_val=evt_val, exec_mode="sniper", level_num=level_num)
    data = await state.get_data()
    
    if data.get("platform") == "appsflyer":
        await callback.message.edit_text(t("sniper_level_prompt", lang))
        await state.set_state(NexusFlow.waiting_for_sniper_lvl)
    else:
        device_os = (data.get("device_os") or "android").upper()
        ad_label = "GAID" if device_os == "ANDROID" else "IDFA"
        third_label = "AID" if device_os == "ANDROID" else "IDFV"
        await callback.message.answer(t("send_ids_prompt", lang, ad_label=ad_label, third_label=third_label))
        await state.set_state(NexusFlow.waiting_for_profile)

@dp.message(NexusFlow.waiting_for_sniper_lvl)
async def process_sniper_lvl(message: types.Message, state: FSMContext):
    await state.update_data(level_num=message.text.strip())
    st = await state.get_data()
    lang = get_user_lang(message.from_user.id)
    device_os = (st.get("device_os") or "android").upper()
    ad_label = "GAID" if device_os == "ANDROID" else "IDFA"
    third_label = "AID" if device_os == "ANDROID" else "IDFV"
    await message.answer(t("send_ids_prompt", lang, ad_label=ad_label, third_label=third_label))
    await state.set_state(NexusFlow.waiting_for_profile)

# --- محرك التنفيذ النهائي (The Execution Engine) ---
@dp.message(NexusFlow.waiting_for_profile)
async def process_execution(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    user_input = message.text.strip()
    data = await state.get_data()
    game, platform, mode = data.get("selected_game"), data.get("platform"), data.get("exec_mode")
    device_os = (data.get("device_os") or "android").strip().lower()
    op_price = get_current_op_price()
    
    # تفكيك المعرفات
    parts = user_input.split("|")
    profile = {"device_os": device_os}
    primary = parts[0].strip() if parts else ""
    if device_os == "ios":
        profile["idfa"] = primary
    else:
        profile["gaid"] = primary

    # محاولة استخدام البيانات المحفوظة إذا كانت المدخلات ناقصة
    user_db = db_get_user(message.from_user.id)
    saved_profile = user_db.profile_data or {}

    proxy_base = (saved_profile.get("proxy_base") or "").strip()
    proxy_username = (saved_profile.get("proxy_username") or "").strip()
    proxy_password = (saved_profile.get("proxy_password") or "").strip()
    proxy_url = _build_proxy_url(proxy_base, proxy_username or None, proxy_password or None) if proxy_base else ""
    if proxy_url:
        profile["proxy_url"] = proxy_url
        profile["proxy_display"] = _mask_proxy_url(proxy_url)
    if mode != "sniper":
        profile = _apply_global_proxy(profile, message.from_user.id)

    # Validation for Adjust/AppsFlyer ID
    if len(parts) > 1 and parts[1].strip():
        profile["af_id"] = profile["adjust_id"] = parts[1].strip()
    else:
        profile["af_id"] = profile["adjust_id"] = None

    # Validation for Android ID
    third_val = parts[2].strip() if len(parts) > 2 else ""
    if device_os == "ios":
        if third_val:
            profile["idfv"] = third_val
        else:
            profile["idfv"] = saved_profile.get("idfv") or None
    else:
        if third_val:
            profile["android_id"] = third_val
        else:
            saved_aid = saved_profile.get("android_id")
            profile["android_id"] = saved_aid if saved_aid else None

    profile["ua"] = parts[3].strip() if len(parts) > 3 and parts[3].strip() else _default_ua(device_os)

    # حفظ البروفايل المؤقت في الحالة لاستخدامه لاحقاً
    await state.update_data(temp_profile=profile)

    # 🆕 التحقق من إعدادات المنصة (Server vs Client)
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    client_enabled = True
    server_enabled = True
    if sys_setting and sys_setting.value:
         client_enabled = sys_setting.value.get(f"{platform}_client", True)
         server_enabled = sys_setting.value.get(f"{platform}_server", True)

    # =========================================================
    # 🆕 النظام الهجين: إيقاف التنفيذ الفوري لـ Sniper وعرض الخيارات
    # =========================================================
    if mode == "sniper":
        builder = InlineKeyboardBuilder()
        if server_enabled:
            builder.row(types.InlineKeyboardButton(text=t("server_mode_btn", lang), callback_data="exec_method:server"))
        if client_enabled:
            builder.row(types.InlineKeyboardButton(text=t("client_mode_btn", lang), callback_data="exec_method:client"))
        builder.row(types.InlineKeyboardButton(text=t("cancel_btn", lang), callback_data="back_to_main"))
        
        if not server_enabled and not client_enabled:
             await message.answer(t("all_methods_disabled", lang), reply_markup=get_back_to_main_kb(lang))
             return

        await message.answer(t("exec_method_prompt", lang), reply_markup=builder.as_markup())
        return # نتوقف هنا وننتظر اختيار المستخدم
    # =========================================================

    # التحقق من الرصيد والجدولة للأوضاع المتقدمة (Natural/Custom/Farm)
    # هذه الأوضاع تعمل دائماً من السيرفر نظراً لتعقيدها
    if not server_enabled:
         await message.answer(t("server_maintenance", lang, platform=platform.title()), reply_markup=get_back_to_main_kb(lang))
         return

    if mode in ["natural", "custom"]:
        steps_count = data.get("timelines_count", len(data.get("custom_plan_steps", [])))
        total_cost = steps_count * op_price
        
        if user_db.balance < total_cost:
            await message.answer(t("balance_low", lang, cost=f"{total_cost:.2f}"), reply_markup=get_back_to_main_kb(lang))
            return

        try:
            profile = _apply_global_proxy(profile, message.from_user.id)
            await send_log(f"[yellow]⚙️ Scheduling {steps_count} events...[/]")
            if mode == "natural":
                session = SessionLocal()
                game_obj = session.query(Game).filter(Game.alias == game).first()
                timelines = session.query(GameTimeline).filter(GameTimeline.game_id == game_obj.id).all()
                session.close()
                nexus_scheduler.schedule_natural_path(kun_engine.send_event, datetime.now(), timelines, [game, profile, message.from_user.id])
            else:
                steps = data.get("custom_plan_steps", [])
                nexus_scheduler.schedule_custom_plan(kun_engine.send_event, datetime.now(), steps, [game, profile, message.from_user.id])

            db_update_user_profile(message.from_user.id, charge_amount=total_cost)
            await message.answer(t("schedule_success", lang), reply_markup=get_back_to_main_kb(lang))

        except Exception as e:
            await message.answer(t("tech_error", lang, e=e), reply_markup=get_back_to_main_kb(lang))

        await state.set_state(NexusFlow.main_menu)
        return

    # Farm Mode Logic (Always Server)
    if user_db.balance < op_price:
        await message.answer(t("balance_low", lang, cost=op_price), reply_markup=get_back_to_main_kb(lang))
        return

    if mode == "farm":
        session = SessionLocal()
        game_obj = session.query(Game).filter(Game.alias == game).first()
        session.close()
        game_config = game_obj.json_data if game_obj else {}
        work_list = [{"lvl": l, "tkn": None} for l in data.get("custom_levels", [])] if platform == "appsflyer" else game_config.get("level_sequence", [])
        profile = _apply_global_proxy(profile, message.from_user.id)
        
        for item in work_list:
            if db_get_user(message.from_user.id).balance < op_price: break
            level_val = item['lvl']
            token_val = item.get('tkn')
            
            # Unpack extended return values
            status, resp, req_h, req_b, res_h, res_time = await kun_engine.send_event(
                game, 
                {"token": token_val, "level": level_val, "name": "level_up"}, 
                profile
            )
            
            db_log_history(
                message.from_user.id, game, platform, f"Farm_{level_val}", status, resp,
                request_headers=req_h, request_body=req_b, response_headers=res_h, response_time_ms=res_time
            )

            if status < 400:
                db_update_user_profile(message.from_user.id, inc_ops=True, charge_amount=op_price)
                await message.answer(t("farm_level_done", lang, level=level_val))
                await asyncio.sleep(15)
            else: 
                await message.answer(t("farm_level_fail", lang, level=level_val))
                break
        await message.answer(t("farm_finished", lang), reply_markup=get_back_to_main_kb(lang))
    
    await state.set_state(NexusFlow.main_menu)

# --- 🆕 معالج اختيار طريقة التنفيذ (Server vs Client) ---
@dp.callback_query(F.data.startswith("exec_method:"))
async def process_execution_choice(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data.split(":")[1]
    data = await state.get_data()
    lang = get_user_lang(callback.from_user.id)
    
    # 🆕 التحقق النهائي من الإعدادات
    platform = data.get("platform")
    session = SessionLocal()
    sys_setting = session.query(SystemSetting).filter(SystemSetting.key == "platforms").first()
    session.close()
    
    if sys_setting and sys_setting.value:
        if not sys_setting.value.get(f"{platform}_{method}", True):
             await callback.message.edit_text(
                 t("method_disabled", lang, method=method.upper(), platform=platform.title()),
                 reply_markup=get_back_to_main_kb(lang)
             )
             return

    game_key = data.get('selected_game')
    profile = data.get('temp_profile')
    evt_val = data.get('evt_val')
    level_num = data.get('level_num')
    op_price = get_current_op_price()
    
    user = db_get_user(callback.from_user.id)
    if user.balance < op_price:
        await callback.message.edit_text(t("balance_low", lang, cost=op_price))
        return

    # تجهيز بيانات الحدث
    event_data = {"name": evt_val, "token": evt_val, "level": level_num}

    if method == "server":
        await callback.message.edit_text(t("server_exec_in_progress", lang))
        profile = _apply_global_proxy(profile, callback.from_user.id)
        status, resp, req_h, req_b, res_h, res_time = await kun_engine.send_event(game_key, event_data, profile)
        
        db_log_history(
            callback.from_user.id, game_key, "Unknown", f"Sniper_{evt_val}", status, resp,
            request_headers=req_h, request_body=req_b, response_headers=res_h, response_time_ms=res_time
        )
        
        if status < 400:
            db_update_user_profile(callback.from_user.id, inc_ops=True, charge_amount=op_price)
            await callback.message.edit_text(
                t("server_exec_success", lang, status=status),
                reply_markup=get_back_to_main_kb(lang)
            )
        else:
            await callback.message.edit_text(
                t("server_exec_failed", lang, status=status, resp=resp),
                reply_markup=get_back_to_main_kb(lang)
            )

    elif method == "client":
        await callback.message.edit_text(t("client_generation_in_progress", lang))
        m_type, result = await kun_engine.generate_client_mission(game_key, event_data, profile)
        
        if m_type == "LINK":
            # Adjust Link
            btn = InlineKeyboardBuilder()
            btn.row(types.InlineKeyboardButton(text=t("client_link_btn", lang), url=result))
            btn.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
            
            db_update_user_profile(callback.from_user.id, inc_ops=True, charge_amount=op_price)
            await callback.message.edit_text(
                t("client_mode_link_intro", lang),
                reply_markup=btn.as_markup()
            )
            
        elif m_type == "CODE":
            # AppsFlyer Code
            db_update_user_profile(callback.from_user.id, inc_ops=True, charge_amount=op_price)
            await callback.message.edit_text(
                t("client_mode_launcher_intro", lang, code=result),
                parse_mode="Markdown",
                reply_markup=get_back_to_main_kb(lang)
            )
        else:
            await callback.message.edit_text(t("client_mode_error", lang), reply_markup=get_back_to_main_kb(lang))

    await state.set_state(NexusFlow.main_menu)

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(c: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(c.from_user.id)
    await state.set_state(NexusFlow.main_menu)
    await c.message.edit_text(t("main_menu_title", lang), reply_markup=get_main_menu_kb(lang, c.from_user.id))

@dp.callback_query(F.data == "back_to_prev")
async def back_to_prev(c: types.CallbackQuery, state: FSMContext):
    await start_search_logic(c, state)

@dp.callback_query(F.data == "chat_menu")
async def open_chat_menu(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await state.set_state(NexusFlow.chat_menu)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("chat_admin_btn", lang), callback_data="chat_admin_start"))
    add_nav_buttons(builder, lang)
    await callback.message.edit_text(t("chat_menu_title", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data == "chat_admin_start")
async def chat_admin_start(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    session = SessionLocal()
    try:
        conv = _get_or_create_user_admin_conversation(session, callback.from_user.id)
    finally:
        session.close()
    await state.update_data(chat_conv_id=conv.id)
    await state.set_state(NexusFlow.chat_waiting_message)
    await callback.message.answer(t("chat_admin_prompt", lang), reply_markup=get_back_to_main_kb(lang))

@dp.message(NexusFlow.chat_waiting_message)
async def chat_send_message(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    body = (message.text or "").strip()
    if not body:
        return

    data = await state.get_data()
    conv_id = data.get("chat_conv_id")
    if not conv_id:
        await message.answer(t("chat_no_conversation", lang), reply_markup=get_back_to_main_kb(lang))
        await state.set_state(NexusFlow.main_menu)
        return

    session = SessionLocal()
    try:
        conv = session.query(ChatConversation).filter(ChatConversation.id == int(conv_id)).first()
        if not conv or conv.is_closed:
            await message.answer(t("chat_conversation_unavailable", lang), reply_markup=get_back_to_main_kb(lang))
            await state.set_state(NexusFlow.main_menu)
            return

        conv_kind = conv.kind
        conv_user_a_id = conv.user_a_id
        conv_user_b_id = conv.user_b_id

        msg = ChatMessage(conversation_id=conv.id, sender_role="user", sender_user_id=message.from_user.id, body=body)
        session.add(msg)
        conv.updated_at = datetime.utcnow()
        session.commit()
    finally:
        session.close()

    if conv_kind == "user_admin":
        admin_id = int(str(os.getenv("ADMIN_ID", "0")).strip() or "0")
        if admin_id and admin_id != 0:
            await bot.send_message(
                admin_id,
                t("chat_support_new_message", "ar", user_id=message.from_user.id, body=body),
                parse_mode="Markdown"
            )
        await message.answer(t("chat_sent", lang))
        return
    await message.answer(t("chat_type_invalid", lang), reply_markup=get_back_to_main_kb(lang))

@dp.callback_query(F.data == "update_gaid_manual")
async def update_gaid_manual(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(t("gaid_update_prompt", lang))
    await state.set_state(NexusFlow.updating_gaid)

@dp.message(NexusFlow.updating_gaid)
async def save_new_gaid(message: types.Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    db_update_user_profile(message.from_user.id, new_gaid=message.text.strip())
    await message.answer(t("gaid_update_success", lang), reply_markup=get_back_to_main_kb(lang))
    await state.set_state(NexusFlow.main_menu)

# --- History Management ---
@dp.callback_query(F.data == "my_history")
async def show_history_menu(callback: types.CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    user_id = callback.from_user.id
    session = SessionLocal()
    # Get last 10 entries
    history_items = session.query(History).filter(History.user_id == user_id).order_by(desc(History.timestamp)).limit(10).all()
    session.close()

    builder = InlineKeyboardBuilder()
    
    if not history_items:
        builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
        await callback.message.edit_text(t("history_empty", lang), reply_markup=builder.as_markup())
        return

    for item in history_items:
        status_icon = "✅" if item.status_code and item.status_code < 400 else "❌"
        # Format: ✅ GameName | 12:30
        time_str = item.timestamp.strftime("%H:%M")
        # Shorten game alias if needed
        g_alias = item.game_alias[:15] if item.game_alias else "Unknown"
        btn_text = f"{status_icon} {g_alias} | {time_str}"
        builder.row(types.InlineKeyboardButton(text=btn_text, callback_data=f"hist_view:{item.id}"))

    builder.row(types.InlineKeyboardButton(text=t("back_main_btn", lang), callback_data="back_to_main"))
    await callback.message.edit_text(t("history_list_title", lang), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("hist_view:"))
async def view_history_item(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    try:
        hist_id = int(callback.data.split(":")[1])
    except:
        await callback.answer(t("history_invalid_id", lang))
        return

    session = SessionLocal()
    item = session.query(History).filter(History.id == hist_id).first()
    session.close()

    if not item:
        await callback.answer(t("history_not_found", lang), show_alert=True)
        return

    # Details
    resp_preview = item.response_text[:200] if item.response_text else "No Response"
    details = t(
        "history_item_details",
        lang,
        item_id=item.id,
        game_alias=item.game_alias,
        platform=item.platform,
        event_name=item.event_name,
        status_code=item.status_code,
        timestamp=item.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        resp_preview=resp_preview
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=t("history_edit_resend_btn", lang), callback_data=f"hist_edit:{item.id}"))
    builder.row(types.InlineKeyboardButton(text=t("history_back_btn", lang), callback_data="my_history"))
    
    await callback.message.edit_text(details, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("hist_edit:"))
async def edit_history_item(callback: types.CallbackQuery, state: FSMContext):
    lang = get_user_lang(callback.from_user.id)
    try:
        hist_id = int(callback.data.split(":")[1])
    except: return

    session = SessionLocal()
    item = session.query(History).filter(History.id == hist_id).first()
    session.close()

    if not item:
        await callback.answer(t("history_not_found", lang), show_alert=True)
        return

    # Attempt to extract Token from request_body or event_name
    token_val = "Unknown"
    if item.event_name and "Sniper_" in item.event_name:
        token_val = item.event_name.replace("Sniper_", "")
    elif item.event_name and "Farm_" not in item.event_name:
        token_val = item.event_name
    
    # If token still unknown, try regex on body
    import re
    body = item.request_body or ""
    if token_val == "Unknown" or token_val == "Server":
        # Adjust
        tm = re.search(r'event_token=([a-zA-Z0-9]+)', body)
        if tm: token_val = tm.group(1)
        else:
            # AppsFlyer
            tm2 = re.search(r'"eventName"\s*:\s*"([^"]+)"', body)
            if tm2: token_val = tm2.group(1)

    # Prepare State for Re-run
    session = SessionLocal()
    try:
        game_obj = session.query(Game).filter(Game.alias == item.game_alias).first()
        device_os = ((game_obj.json_data or {}).get("device_os") if game_obj else None) or "android"
    finally:
        session.close()

    await state.update_data(
        selected_game=item.game_alias, 
        platform=item.platform.lower() if item.platform else "unknown",
        device_os=str(device_os).lower(),
        exec_mode="sniper", # Force sniper for single re-run
        evt_val=token_val,
        level_num="0" # Default
    )

    msg = t(
        "history_edit_header",
        lang,
        game_alias=item.game_alias,
        token_val=token_val
    )
    await callback.message.edit_text(msg)
    await state.set_state(NexusFlow.waiting_for_profile)


# ==============================================================
# 🔥 الحل الجذري لمشكلة عدم عمل لوحة التحكم والتعليمات
# يتم استدعاء ملفات التسجيل في النهاية لمنع التداخل (Circular Import)
# ==============================================================
try:
    from .educational import register_edu_handlers, get_edu_menu_kb
    from .admin_panel import register_admin_handlers
    
    register_admin_handlers(dp)
    register_edu_handlers(dp)
except Exception as e:
    print(f"Error registering external handlers: {e}")
    print(f"Error registering external handlers: {e}")
