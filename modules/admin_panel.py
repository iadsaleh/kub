import os
import asyncio
import secrets
import json
import datetime
from aiogram import types, F, Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .database import (
    SessionLocal,
    User,
    History,
    Coupon,
    Game,
    GameTimeline,
    ChatConversation,
    ChatMessage,
    Transaction,
    SystemSetting,
    ChangeLog,
    StoreCategory,
    StoreItem,
)
# استيراد أداة النسخ الاحتياطي الجديدة
try:
    from .backup_utils import send_backup_to_admin
except ImportError:
    send_backup_to_admin = None

# --- تعريف الحالات (States) ---

# 1. حالات إدارة النظام والمالية والخطط
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()
    waiting_for_global_amount = State()
    waiting_for_op_price = State()
    waiting_for_broadcast_msg = State()
    waiting_for_coupon_amount = State()
    # حالات إدارة الخطط الزمنية
    waiting_for_timeline_data = State()
    waiting_for_chat_reply = State()
    waiting_for_store_category_name = State()
    waiting_for_store_item_title = State()
    waiting_for_store_item_price = State()
    waiting_for_store_item_desc = State()

# 2. حالات إضافة لعبة جديدة (Wizard)
class AddGameFlow(StatesGroup):
    waiting_for_name = State()
    waiting_for_package = State()
    waiting_for_provider = State()
    waiting_for_key = State()
    waiting_for_advanced_data = State()

# --- أدوات مساعدة ---
def get_current_op_price():
    try:
        if os.path.exists("op_price.txt"):
            with open("op_price.txt", "r") as f:
                return float(f.read().strip())
        return 0.05
    except: return 0.05

# --- القائمة الرئيسية للمدير ---
def get_admin_main_kb():
    builder = InlineKeyboardBuilder()
    # الصف الأول: الإضافات والمستخدمين
    builder.row(types.InlineKeyboardButton(text="➕ إضافة لعبة جديدة", callback_data="admin:add_game"),
                types.InlineKeyboardButton(text="👥 المستخدمين", callback_data="admin:view_users"))
    
    # الصف الثاني: الإدارة المالية (جديد)
    builder.row(types.InlineKeyboardButton(text="💰 الإدارة المالية", callback_data="admin:finance_menu"))

    builder.row(types.InlineKeyboardButton(text="🛒 إدارة المتجر", callback_data="admin:store_menu"))
    
    # الصف الثالث: التحكم
    builder.row(types.InlineKeyboardButton(text="📅 الخطط الزمنية", callback_data="admin:manage_timelines"),
                types.InlineKeyboardButton(text="💰 سعر العملية", callback_data="admin:set_price"))

    # الصف الرابع: التواصل
    builder.row(types.InlineKeyboardButton(text="💬 الرسائل", callback_data="admin:chat_inbox"),
                types.InlineKeyboardButton(text="📢 إذاعة", callback_data="admin:broadcast"))
    
    # الصف الخامس: الأدوات والنسخ الاحتياطي
    builder.row(types.InlineKeyboardButton(text="📦 نسخة احتياطية", callback_data="admin:backup_now"))
    
    return builder.as_markup()

def _ensure_default_store_categories(session):
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

def get_store_admin_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📥 محتوى القناة (غير منشور)", callback_data="admin:store_inbox:0"))
    builder.row(types.InlineKeyboardButton(text="📦 المحتوى المنشور", callback_data="admin:store_published:0"))
    builder.row(types.InlineKeyboardButton(text="🗂️ الأقسام", callback_data="admin:store_categories"))
    builder.row(types.InlineKeyboardButton(text="🔙 عودة", callback_data="back_to_main"))
    return builder.as_markup()

async def open_store_admin_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛒 **إدارة المتجر:**\nاختر العملية:", reply_markup=get_store_admin_kb(), parse_mode="Markdown")

async def store_inbox(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        page = int(parts[-1])
    except Exception:
        page = 0
    page = max(0, page)
    page_size = 10

    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        q = session.query(StoreItem).filter(StoreItem.is_active == False).order_by(StoreItem.created_at.desc())
        total = q.count()
        items = q.offset(page * page_size).limit(page_size).all()

        builder = InlineKeyboardBuilder()
        for it in items:
            title = (it.title or "").strip()[:40] or f"Item {it.id}"
            builder.row(types.InlineKeyboardButton(text=f"#{it.id} | {title}", callback_data=f"admin:store_item:{it.id}:{page}"))
        if total > (page + 1) * page_size:
            builder.row(types.InlineKeyboardButton(text="التالي ➡️", callback_data=f"admin:store_inbox:{page+1}"))
        if page > 0:
            builder.row(types.InlineKeyboardButton(text="⬅️ السابق", callback_data=f"admin:store_inbox:{page-1}"))
        builder.row(types.InlineKeyboardButton(text="🔙 عودة", callback_data="admin:store_menu"))
        await callback.message.edit_text("📥 **محتوى القناة (غير منشور):**", reply_markup=builder.as_markup(), parse_mode="Markdown")
    finally:
        session.close()

async def store_published(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        page = int(parts[-1])
    except Exception:
        page = 0
    page = max(0, page)
    page_size = 10

    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        q = session.query(StoreItem).filter(StoreItem.is_active == True).order_by(StoreItem.created_at.desc())
        total = q.count()
        items = q.offset(page * page_size).limit(page_size).all()

        builder = InlineKeyboardBuilder()
        for it in items:
            title = (it.title or "").strip()[:40] or f"Item {it.id}"
            builder.row(types.InlineKeyboardButton(text=f"#{it.id} | {title}", callback_data=f"admin:store_item_pub:{it.id}:{page}"))
        if total > (page + 1) * page_size:
            builder.row(types.InlineKeyboardButton(text="التالي ➡️", callback_data=f"admin:store_published:{page+1}"))
        if page > 0:
            builder.row(types.InlineKeyboardButton(text="⬅️ السابق", callback_data=f"admin:store_published:{page-1}"))
        builder.row(types.InlineKeyboardButton(text="🔙 عودة", callback_data="admin:store_menu"))
        await callback.message.edit_text("📦 **المحتوى المنشور:**", reply_markup=builder.as_markup(), parse_mode="Markdown")
    finally:
        session.close()

async def store_item_view(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        scope = "inbox"
        if len(parts) >= 4 and parts[1] == "store_item_pub":
            scope = "pub"
            item_id = int(parts[2])
            page = int(parts[3])
        else:
            item_id = int(parts[2])
            page = int(parts[3])
    except Exception:
        await callback.answer("❌ غير صالح", show_alert=True)
        return

    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        it = session.query(StoreItem).filter(StoreItem.id == item_id).first()
        if not it:
            await callback.answer("❌ غير موجود", show_alert=True)
            return
        cat = session.query(StoreCategory).filter(StoreCategory.id == it.category_id).first() if it.category_id else None
        cat_name = cat.name if cat else "غير محدد"
        price = float(it.price or 0.0)
        status = "✅ منشور" if it.is_active else "⛔ غير منشور"
        text = (
            f"🧾 **عنصر المتجر #{it.id}**\n"
            f"العنوان: `{(it.title or '').strip()}`\n"
            f"الوصف: `{((it.description or '').strip()[:60]) or '—'}`\n"
            f"القسم: `{cat_name}`\n"
            f"السعر: `${price:.2f}`\n"
            f"الحالة: {status}\n"
            f"النوع: `{it.file_type}`\n"
            f"المصدر: `{it.source_chat_id}:{it.source_message_id}`"
        )
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="✏️ تعديل العنوان", callback_data=f"admin:store_set_title:{it.id}:{page}"))
        builder.row(types.InlineKeyboardButton(text="📝 تعديل الوصف", callback_data=f"admin:store_set_desc:{it.id}:{scope}:{page}"))
        builder.row(types.InlineKeyboardButton(text="💵 تعديل السعر", callback_data=f"admin:store_set_price:{it.id}:{page}"))
        builder.row(types.InlineKeyboardButton(text="🗂️ تعيين القسم", callback_data=f"admin:store_choose_cat:{it.id}:{page}:{scope}"))
        builder.row(types.InlineKeyboardButton(text="👁️ عرض الملف", callback_data=f"admin:store_preview:{it.id}:{page}"))
        if scope == "pub":
            builder.row(types.InlineKeyboardButton(text="✅ نشر/إيقاف", callback_data=f"admin:store_toggle_pub:{it.id}:{page}"))
        else:
            builder.row(types.InlineKeyboardButton(text="✅ نشر/إيقاف", callback_data=f"admin:store_toggle:{it.id}:{page}"))
        builder.row(types.InlineKeyboardButton(text="🗑️ حذف", callback_data=f"admin:store_delete_confirm:{it.id}:{scope}:{page}"))
        if scope == "pub":
            builder.row(types.InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"admin:store_published:{page}"))
        else:
            builder.row(types.InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"admin:store_inbox:{page}"))
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    finally:
        session.close()

async def store_item_preview(callback: types.CallbackQuery):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == item_id).first()
        if not it:
            await callback.answer("❌", show_alert=True)
            return
        caption = (it.title or "").strip()
        ft = (it.file_type or "").lower()
        if ft == "document":
            await callback.message.answer_document(it.file_id, caption=caption)
        elif ft == "video":
            await callback.message.answer_video(it.file_id, caption=caption)
        elif ft == "audio":
            await callback.message.answer_audio(it.file_id, caption=caption)
        elif ft == "voice":
            await callback.message.answer_voice(it.file_id, caption=caption)
        elif ft == "photo":
            await callback.message.answer_photo(it.file_id, caption=caption)
        else:
            await callback.message.answer(caption)
        await callback.answer("✅")
    finally:
        session.close()

async def store_item_toggle(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        scope = "inbox"
        if len(parts) >= 4 and parts[1] == "store_toggle_pub":
            scope = "pub"
            item_id = int(parts[2])
            page = int(parts[3])
        else:
            item_id = int(parts[2])
            page = int(parts[3])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == item_id).first()
        if not it:
            await callback.answer("❌", show_alert=True)
            return
        if not it.is_active:
            if not it.category_id or float(it.price or 0.0) <= 0:
                await callback.answer("يجب تعيين القسم والسعر أولاً", show_alert=True)
                return
        it.is_active = not bool(it.is_active)
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Store Item Toggle", details={"item_id": it.id, "is_active": it.is_active}))
        session.commit()
        await callback.answer("✅", show_alert=True)
    except Exception:
        session.rollback()
        await callback.answer("❌", show_alert=True)
    finally:
        session.close()
    if scope == "pub":
        callback.data = f"admin:store_item_pub:{item_id}:{page}"
    else:
        callback.data = f"admin:store_item:{item_id}:{page}"
    await store_item_view(callback, state)

async def store_start_set_desc(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        scope = parts[3]
        page = int(parts[4])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    await state.update_data(store_item_id=item_id, store_item_scope=scope, store_item_page=page)
    await state.set_state(AdminStates.waiting_for_store_item_desc)
    await callback.message.answer("📝 أرسل الوصف الجديد (أرسل - لمسح الوصف):")
    await callback.answer()

async def store_set_desc(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get("store_item_id")
    if not item_id:
        await state.clear()
        return
    text = (message.text or "").strip()
    desc = None
    if text and text != "-":
        desc = text[:2000]
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == int(item_id)).first()
        if it:
            it.description = desc
            session.add(ChangeLog(editor=str(message.from_user.id), action="Store Item Set Description", details={"item_id": it.id}))
            session.commit()
    finally:
        session.close()
    await message.answer("✅ تم تحديث الوصف.", reply_markup=get_admin_main_kb())
    await state.clear()

async def store_delete_confirm(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        scope = parts[3]
        page = int(parts[4])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ تأكيد الحذف", callback_data=f"admin:store_delete:{item_id}:{scope}:{page}"))
    if scope == "pub":
        builder.row(types.InlineKeyboardButton(text="⬅️ إلغاء", callback_data=f"admin:store_item_pub:{item_id}:{page}"))
    else:
        builder.row(types.InlineKeyboardButton(text="⬅️ إلغاء", callback_data=f"admin:store_item:{item_id}:{page}"))
    await callback.message.edit_text(f"⚠️ حذف عنصر المتجر #{item_id}؟", reply_markup=builder.as_markup())
    await callback.answer()

async def store_delete_do(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        scope = parts[3]
        page = int(parts[4])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == int(item_id)).first()
        if not it:
            await callback.answer("❌ غير موجود", show_alert=True)
            return
        purchased = session.query(func.count(StorePurchase.id)).filter(StorePurchase.item_id == int(item_id)).scalar() or 0
        if int(purchased) > 0:
            await callback.answer("لا يمكن حذف عنصر تم شراؤه", show_alert=True)
            return
        session.delete(it)
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Store Item Delete", details={"item_id": int(item_id)}))
        session.commit()
    except Exception:
        session.rollback()
        await callback.answer("❌ فشل الحذف", show_alert=True)
        return
    finally:
        session.close()
    await callback.answer("✅ تم الحذف", show_alert=True)
    if scope == "pub":
        callback.data = f"admin:store_published:{page}"
        await store_published(callback, state)
    else:
        callback.data = f"admin:store_inbox:{page}"
        await store_inbox(callback, state)

async def store_choose_category(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        page = int(parts[3])
        scope = parts[4] if len(parts) > 4 else "inbox"
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        cats = session.query(StoreCategory).filter(StoreCategory.is_active == True).order_by(StoreCategory.sort_order.asc(), StoreCategory.id.asc()).all()
        builder = InlineKeyboardBuilder()
        for c in cats:
            builder.row(types.InlineKeyboardButton(text=c.name, callback_data=f"admin:store_set_cat:{item_id}:{c.id}:{page}:{scope}"))
        if scope == "pub":
            builder.row(types.InlineKeyboardButton(text="🔙 رجوع", callback_data=f"admin:store_item_pub:{item_id}:{page}"))
        else:
            builder.row(types.InlineKeyboardButton(text="🔙 رجوع", callback_data=f"admin:store_item:{item_id}:{page}"))
        await callback.message.edit_text("🗂️ اختر القسم:", reply_markup=builder.as_markup())
    finally:
        session.close()

async def store_set_category(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        cat_id = int(parts[3])
        page = int(parts[4])
        scope = parts[5] if len(parts) > 5 else "inbox"
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == item_id).first()
        cat = session.query(StoreCategory).filter(StoreCategory.id == cat_id, StoreCategory.is_active == True).first()
        if not it or not cat:
            await callback.answer("❌", show_alert=True)
            return
        it.category_id = cat.id
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Store Item Set Category", details={"item_id": it.id, "category_id": cat.id}))
        session.commit()
        await callback.answer("✅", show_alert=True)
    except Exception:
        session.rollback()
        await callback.answer("❌", show_alert=True)
        return
    finally:
        session.close()
    if scope == "pub":
        callback.data = f"admin:store_item_pub:{item_id}:{page}"
    else:
        callback.data = f"admin:store_item:{item_id}:{page}"
    await store_item_view(callback, state)

async def store_start_set_title(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    await state.update_data(store_item_id=item_id, store_item_page=page)
    await state.set_state(AdminStates.waiting_for_store_item_title)
    await callback.message.answer("✏️ أرسل العنوان الجديد:")
    await callback.answer()

async def store_set_title(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get("store_item_id")
    if not item_id:
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        return
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == int(item_id)).first()
        if it:
            it.title = title[:200]
            session.add(ChangeLog(editor=str(message.from_user.id), action="Store Item Set Title", details={"item_id": it.id}))
            session.commit()
    finally:
        session.close()
    await message.answer("✅ تم تحديث العنوان.", reply_markup=get_admin_main_kb())
    await state.clear()

async def store_start_set_price(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    await state.update_data(store_item_id=item_id, store_item_page=page)
    await state.set_state(AdminStates.waiting_for_store_item_price)
    await callback.message.answer("💵 أرسل السعر بالدولار (مثال: 5.00):")
    await callback.answer()

async def store_set_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get("store_item_id")
    if not item_id:
        await state.clear()
        return
    try:
        price = float((message.text or "").strip())
    except Exception:
        await message.answer("❌ سعر غير صالح.")
        return
    if price < 0:
        price = 0.0
    session = SessionLocal()
    try:
        it = session.query(StoreItem).filter(StoreItem.id == int(item_id)).first()
        if it:
            it.price = price
            session.add(ChangeLog(editor=str(message.from_user.id), action="Store Item Set Price", details={"item_id": it.id, "price": price}))
            session.commit()
    finally:
        session.close()
    await message.answer("✅ تم تحديث السعر.", reply_markup=get_admin_main_kb())
    await state.clear()

async def store_categories_menu(callback: types.CallbackQuery, state: FSMContext):
    session = SessionLocal()
    try:
        _ensure_default_store_categories(session)
        cats = session.query(StoreCategory).order_by(StoreCategory.sort_order.asc(), StoreCategory.id.asc()).all()
        builder = InlineKeyboardBuilder()
        for c in cats:
            status = "✅" if c.is_active else "⛔"
            builder.row(types.InlineKeyboardButton(text=f"{status} {c.name}", callback_data=f"admin:store_cat_toggle:{c.id}"))
        builder.row(types.InlineKeyboardButton(text="➕ إضافة قسم", callback_data="admin:store_cat_add"))
        builder.row(types.InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:store_menu"))
        await callback.message.edit_text("🗂️ **أقسام المتجر:**\nاضغط لتفعيل/إيقاف قسم.", reply_markup=builder.as_markup(), parse_mode="Markdown")
    finally:
        session.close()

async def store_category_toggle(callback: types.CallbackQuery):
    parts = (callback.data or "").split(":")
    try:
        cat_id = int(parts[2])
    except Exception:
        await callback.answer("❌", show_alert=True)
        return
    session = SessionLocal()
    try:
        c = session.query(StoreCategory).filter(StoreCategory.id == cat_id).first()
        if not c:
            await callback.answer("❌", show_alert=True)
            return
        c.is_active = not bool(c.is_active)
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Store Category Toggle", details={"category_id": c.id, "is_active": c.is_active}))
        session.commit()
        await callback.answer("✅", show_alert=True)
    except Exception:
        session.rollback()
        await callback.answer("❌", show_alert=True)
    finally:
        session.close()

async def store_category_add_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_store_category_name)
    await callback.message.answer("➕ أرسل اسم القسم الجديد:")
    await callback.answer()

async def store_category_add(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        return
    slug = "".join([ch.lower() if ch.isalnum() else "_" for ch in name]).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    if not slug:
        slug = secrets.token_hex(4)
    session = SessionLocal()
    try:
        if session.query(StoreCategory).filter(StoreCategory.slug == slug).first():
            slug = f"{slug}_{secrets.token_hex(2)}"
        session.add(StoreCategory(name=name[:120], slug=slug[:120], sort_order=1000, is_active=True))
        session.add(ChangeLog(editor=str(message.from_user.id), action="Store Category Add", details={"slug": slug}))
        session.commit()
        await message.answer("✅ تم إضافة القسم.", reply_markup=get_admin_main_kb())
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ خطأ: {e}", reply_markup=get_admin_main_kb())
    finally:
        session.close()
        await state.clear()

def get_finance_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📊 الإحصائيات المالية", callback_data="admin:finance_stats"))
    builder.row(types.InlineKeyboardButton(text="👤 شحن رصيد", callback_data="admin:add_balance"),
                types.InlineKeyboardButton(text="🎟️ كوبون جديد", callback_data="admin:gen_coupon"))
    builder.row(types.InlineKeyboardButton(text="⚠️ تصفير جميع المحافظ", callback_data="admin:reset_wallets_auth"))
    builder.row(types.InlineKeyboardButton(text="🔄 تصفير الصرفيات", callback_data="admin:reset_currency_auth"))
    builder.row(types.InlineKeyboardButton(text="💲 تحديد سعر الطلب", callback_data="admin:set_request_price"))
    builder.row(types.InlineKeyboardButton(text="🔙 عودة", callback_data="back_to_main"))
    return builder.as_markup()

# --- نقطة الدخول ---
async def admin_access_handler(message: types.Message):
    # تحقق من ID المدير
    target_admin = str(os.getenv("ADMIN_ID", "0")).strip()
    current_user = str(message.from_user.id).strip()
    
    if target_admin == "0" or current_user == target_admin:
        await message.answer("🛠️ **لوحة التحكم الشاملة:**", reply_markup=get_admin_main_kb())
    else:
        await message.answer("❌ غير مصرح لك بالدخول إلى لوحة الأدمن.")

async def admin_open_from_button(callback: types.CallbackQuery, state: FSMContext):
    target_admin = str(os.getenv("ADMIN_ID", "0")).strip()
    current_user = str(callback.from_user.id).strip()
    if target_admin == "0" or current_user == target_admin:
        await state.clear()
        await callback.message.edit_text("🛠️ **لوحة التحكم الشاملة:**", reply_markup=get_admin_main_kb())
        await callback.answer()
        return
    await callback.answer("❌ غير مصرح", show_alert=True)

async def back_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠️ **لوحة التحكم الشاملة:**", reply_markup=get_admin_main_kb())

# ====================================================
#  PART 1: إضافة لعبة جديدة (Add Game Logic)
# ====================================================

async def start_add_game(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📝 **1/5: اسم اللعبة:**\n(مثال: Coin Master)", 
                                     reply_markup=InlineKeyboardBuilder().row(types.InlineKeyboardButton(text="🔙 إلغاء", callback_data="back_to_main")).as_markup())
    await state.set_state(AddGameFlow.waiting_for_name)

async def process_game_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    alias = name.lower().replace(" ", "_")
    await state.update_data(name=name, alias=alias)
    await message.answer(f"✅ الاسم: {name}\n\n📦 **2/5: Package Name:**\n(مثال: com.moonactive.coinmaster)")
    await state.set_state(AddGameFlow.waiting_for_package)

async def process_package(message: types.Message, state: FSMContext):
    pkg = message.text.strip()
    await state.update_data(app_id=pkg)
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔵 AppsFlyer", callback_data="prov:AppsFlyer"))
    builder.row(types.InlineKeyboardButton(text="🔴 Adjust", callback_data="prov:Adjust"))
    
    await message.answer("📡 **3/5: اختر المزود:**", reply_markup=builder.as_markup())
    await state.set_state(AddGameFlow.waiting_for_provider)

async def process_provider(callback: types.CallbackQuery, state: FSMContext):
    provider = callback.data.split(":")[1]
    await state.update_data(provider=provider)
    
    lbl = "Dev Key" if provider == "AppsFlyer" else "App Token"
    await callback.message.edit_text(f"🔑 **4/5: أدخل {lbl}:**\n(المفتاح الرئيسي للعبة)")
    await state.set_state(AddGameFlow.waiting_for_key)

async def process_key_step(message: types.Message, state: FSMContext):
    key_val = message.text.strip()
    await state.update_data(main_key=key_val)
    data = await state.get_data()
    
    if data['provider'] == "AppsFlyer":
        text = (
            "⚙️ **5/5: إعداد قالب الإرسال (Template):**\n\n"
            "أرسل **'default'** لاستخدام القالب الافتراضي.\n"
            "أو أرسل JSON مخصص مع `{LEVEL}`."
        )
    else: # Adjust
        text = (
            "⚙️ **5/5: إعداد توكنات الأحداث (Event Tokens):**\n\n"
            "أرسل التوكنات بصيغة: `الاسم:التوكن` (كل واحد في سطر).\n"
            "مثال:\n`Level 10:abc123`"
        )
    
    await message.answer(text)
    await state.set_state(AddGameFlow.waiting_for_advanced_data)

async def process_advanced_and_save(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    data = await state.get_data()
    
    json_data = {
        "app_id": data['app_id'],
        "provider": data['provider']
    }
    
    if data['provider'] == "AppsFlyer":
        json_data["dev_key"] = data['main_key']
        template_str = "{\"af_level\":\"{LEVEL}\",\"af_score\":100}" if user_input.lower() == "default" else user_input
        json_data["event_templates"] = {
            "level_up": {
                "event_name": "af_level_achieved",
                "json_template": template_str
            }
        }
    else:
        json_data["app_token"] = data['main_key']
        json_data["level_sequence"] = []
        lines = user_input.split('\n')
        for line in lines:
            if ":" in line:
                parts = line.split(":")
                json_data["level_sequence"].append({"lvl": parts[0].strip(), "tkn": parts[1].strip()})

    session = SessionLocal()
    try:
        if session.query(Game).filter(Game.alias == data['alias']).first():
            await message.answer("❌ اللعبة موجودة مسبقاً!")
        else:
            new_game = Game(
                alias=data['alias'], name=data['name'],
                provider=data['provider'], json_data=json_data, is_active=True
            )
            session.add(new_game)
            session.commit()
            await message.answer(f"✅ **تمت إضافة {data['name']} بنجاح!**", reply_markup=get_admin_main_kb())
    except Exception as e:
        await message.answer(f"❌ خطأ: {e}")
    finally:
        session.close()
        await state.clear()


# ====================================================
#  PART 2: إدارة الخطط الزمنية (Timelines) [محدث مع صفحات]
# ====================================================

GAMES_PER_PAGE = 10 

async def start_manage_timelines(callback: types.CallbackQuery, state: FSMContext):
    # تحليل رقم الصفحة
    data_parts = callback.data.split(":")
    page = int(data_parts[-1]) if "page" in callback.data else 0

    session = SessionLocal()
    all_games = session.query(Game).order_by(Game.name).all()
    session.close()
    
    if not all_games:
        await callback.answer("⚠️ لا توجد ألعاب.", show_alert=True)
        return

    # منطق التقسيم (Pagination)
    total_games = len(all_games)
    start_index = page * GAMES_PER_PAGE
    end_index = start_index + GAMES_PER_PAGE
    current_page_games = all_games[start_index:end_index]

    builder = InlineKeyboardBuilder()
    for g in current_page_games:
        display_name = g.name if g.name else g.alias
        builder.row(types.InlineKeyboardButton(text=f"🎮 {display_name}", callback_data=f"admin:edit_tl:{g.id}"))
    
    # أزرار التنقل
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton(text="➡️ السابق", callback_data=f"admin:manage_timelines:page:{page-1}"))
    if end_index < total_games:
        nav_buttons.append(types.InlineKeyboardButton(text="التالي ⬅️", callback_data=f"admin:manage_timelines:page:{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(types.InlineKeyboardButton(text="⬅️ عودة", callback_data="back_to_main"))
    
    await callback.message.edit_text(f"🎯 **ضبط الخطط (صفحة {page + 1}):**\nاختر اللعبة:", reply_markup=builder.as_markup())

async def process_timeline_edit(callback: types.CallbackQuery, state: FSMContext):
    game_id = int(callback.data.split(':')[-1])
    await state.update_data(edit_game_id=game_id)
    await callback.message.answer(
        "📅 **أرسل خطوات المسار الطبيعي:**\n`الاسم | القيمة | أيام | ساعات`\nمثال:\n`Level 5 | 5 | 0 | 2`",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_timeline_data)

async def save_timeline_data(message: types.Message, state: FSMContext):
    data = await state.get_data()
    game_id = data.get('edit_game_id')
    lines = message.text.strip().split('\n')
    
    session = SessionLocal()
    try:
        session.query(GameTimeline).filter(GameTimeline.game_id == game_id).delete()
        count = 0
        for line in lines:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 4:
                session.add(GameTimeline(
                    game_id=game_id, step_name=parts[0], event_value=parts[1], event_token=parts[1],
                    day_offset=int(parts[2]), hour_offset=int(parts[3])
                ))
                count += 1
        session.commit()
        await message.answer(f"✅ تم حفظ {count} خطوة.", reply_markup=get_admin_main_kb())
    except Exception as e:
        await message.answer(f"❌ خطأ: {e}")
    finally:
        session.close()
        await state.clear()


# ====================================================
#  PART 3: الإحصائيات والمالية والنسخ الاحتياطي
# ====================================================

async def show_admin_stats(callback: types.CallbackQuery):
    session = SessionLocal()
    u_count = session.query(User).count()
    ops_count = session.query(History).filter(History.status_code < 400).count()
    total_bal = sum(u.balance for u in session.query(User).all())
    session.close()
    
    text = f"📊 **الإحصائيات:**\n👥 المستخدمين: `{u_count}`\n✅ عمليات ناجحة: `{ops_count}`\n💰 إجمالي الأرصدة: `${total_bal:.2f}`"
    await callback.message.edit_text(text, reply_markup=get_admin_main_kb())

async def start_add_balance(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📥 أرسل ID المستخدم:")
    await state.set_state(AdminStates.waiting_for_user_id)

async def process_user_id(message: types.Message, state: FSMContext):
    await state.update_data(target_id=message.text.strip())
    await message.answer("💵 المبلغ:")
    await state.set_state(AdminStates.waiting_for_amount)

async def final_add_balance(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        t_id, amt = int(data["target_id"]), float(message.text.strip())
        session = SessionLocal()
        user = session.query(User).filter(User.id == t_id).first()
        if user:
            user.balance += amt
            try:
                trans = Transaction(
                    user_id=t_id,
                    amount=amt,
                    type="DEPOSIT",
                    source="ADMIN",
                    description="Admin recharge via bot",
                )
                session.add(trans)
            except Exception:
                pass
            session.commit()
            await message.answer(f"✅ تم شحن `${amt}`.")
            try:
                await bot.send_message(t_id, f"🎊 تم شحن رصيدك: `${amt}`")
            except Exception:
                pass
        else:
            await message.answer("❌ مستخدم غير موجود.")
        session.close()
    except Exception:
        pass
    await state.clear()

async def start_gen_coupon(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("💵 قيمة الكوبون:")
    await state.set_state(AdminStates.waiting_for_coupon_amount)

async def process_gen_coupon(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.strip())
        code = f"KUN-{secrets.token_hex(4).upper()}"
        session = SessionLocal()
        session.add(Coupon(code=code, amount=amt))
        session.commit()
        session.close()
        await message.answer(f"🎟️ الكود: `{code}`\nالقيمة: `${amt}`")
    except: pass
    await state.clear()

async def start_set_price(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("💵 السعر الجديد:")
    await state.set_state(AdminStates.waiting_for_op_price)

async def final_set_price(message: types.Message, state: FSMContext):
    try:
        raw = message.text.strip()
        with open("op_price.txt", "w") as f:
            f.write(raw)

        try:
            price = float(raw)
            session = SessionLocal()
            try:
                setting = session.query(SystemSetting).filter(SystemSetting.key == "request_price").first()
                if not setting:
                    setting = SystemSetting(key="request_price", value=price)
                    session.add(setting)
                else:
                    setting.value = price
                session.add(ChangeLog(editor=str(message.from_user.id), action="Update Request Price", details={"price": price}))
                session.commit()
            finally:
                session.close()
        except Exception:
            pass
        await message.answer("✅ تم تحديث السعر.")
    except: pass
    await state.clear()

async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📢 نص الرسالة:")
    await state.set_state(AdminStates.waiting_for_broadcast_msg)

async def final_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    session = SessionLocal()
    users = session.query(User).all()
    c = 0
    for u in users:
        try:
            await bot.send_message(u.id, f"📢 **إشعار:**\n{message.text}")
            c += 1
            await asyncio.sleep(0.05)
        except: pass
    session.close()
    await message.answer(f"✅ تم الإرسال لـ {c}.")
    await state.clear()

async def view_all_users(callback: types.CallbackQuery):
    session = SessionLocal()
    users = session.query(User).all()
    session.close()
    msg = "\n".join([f"🆔 `{u.id}` | 💰 `${u.balance:.2f}`" for u in users[:20]])
    await callback.message.edit_text(f"👥 **المستخدمين (آخر 20):**\n{msg}", reply_markup=get_admin_main_kb())

async def open_finance_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("💰 **الإدارة المالية:**\nاختر العملية:", reply_markup=get_finance_kb())

async def reset_wallets_handler(callback: types.CallbackQuery):
    session = SessionLocal()
    try:
        session.query(User).update({User.balance: 0.0})
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Reset Wallets", details={"all": True}))
        session.commit()
        await callback.answer("✅ تم تصفير جميع المحافظ بنجاح!", show_alert=True)
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        await callback.answer(f"❌ حدث خطأ: {e}", show_alert=True)
    finally:
        session.close()

async def reset_currency_handler(callback: types.CallbackQuery):
    session = SessionLocal()
    try:
        users = session.query(User).all()
        updated = 0
        for u in users:
            if not u.profile_data or "total_ops" not in u.profile_data:
                continue
            p = dict(u.profile_data)
            if p.get("total_ops") != 0:
                p["total_ops"] = 0
                u.profile_data = p
                updated += 1
        session.add(ChangeLog(editor=str(callback.from_user.id), action="Reset Currency", details={"users_updated": updated}))
        session.commit()
        await callback.answer(f"✅ تم تصفير الصرفيات لـ {updated} مستخدم.", show_alert=True)
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        await callback.answer(f"❌ حدث خطأ: {e}", show_alert=True)
    finally:
        session.close()

async def trigger_manual_backup(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    if send_backup_to_admin is None:
        await callback.answer("❌ ميزة النسخ الاحتياطي غير متاحة حالياً.", show_alert=True)
        return
    await callback.answer("⏳ جاري إنشاء النسخة...", show_alert=False)
    admin_id = callback.from_user.id
    try:
        await send_backup_to_admin(bot, admin_id)
        await callback.message.answer("✅ تم إرسال النسخة الاحتياطية بنجاح.")
    except Exception as e:
        await callback.message.answer(f"❌ فشل النسخ: {e}")

async def admin_chat_inbox(callback: types.CallbackQuery, state: FSMContext):
    session = SessionLocal()
    try:
        convs = session.query(ChatConversation).filter(ChatConversation.kind == "user_admin", ChatConversation.is_closed == False).order_by(ChatConversation.updated_at.desc()).limit(20).all()
        builder = InlineKeyboardBuilder()
        

        for c in convs:
            last = session.query(ChatMessage).filter(ChatMessage.conversation_id == c.id).order_by(ChatMessage.created_at.desc()).first()
            preview = (last.body[:20] + "...") if last and last.body and len(last.body) > 20 else (last.body if last else "")
            builder.row(types.InlineKeyboardButton(text=f"💬 {c.user_a_id} | {preview}", callback_data=f"admin:chat_open:{c.id}"))

        builder.row(types.InlineKeyboardButton(text="⬅️ عودة", callback_data="back_to_main"))
        if not convs:
            await callback.message.edit_text("📭 لا توجد رسائل دعم حالياً.", reply_markup=builder.as_markup())
            return
        await callback.message.edit_text("💬 صندوق الدعم:", reply_markup=builder.as_markup())
    finally:
        session.close()

async def admin_chat_open(callback: types.CallbackQuery, state: FSMContext):
    try:
        conv_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("❌ معرف غير صالح")
        return

    session = SessionLocal()
    try:
        conv = session.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
        if not conv:
            await callback.answer("❌ المحادثة غير موجودة", show_alert=True)
            return
        msgs = session.query(ChatMessage).filter(ChatMessage.conversation_id == conv.id).order_by(ChatMessage.created_at.desc()).limit(10).all()
        msgs = list(reversed(msgs))
        lines = []
        for m in msgs:
            who = "ADMIN" if m.sender_role == "admin" else str(m.sender_user_id or "USER")
            lines.append(f"{who}: {m.body}")
        text = "💬 محادثة دعم\n" + f"User: `{conv.user_a_id}`\n\n" + "\n".join(lines[-10:]) + "\n\n✍️ أرسل ردك الآن:"
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="✅ إغلاق المحادثة", callback_data=f"admin:chat_close:{conv.id}"))
        builder.row(types.InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:chat_inbox"))
        await state.update_data(admin_chat_conv_id=conv.id)
        await state.set_state(AdminStates.waiting_for_chat_reply)
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    finally:
        session.close()

async def admin_chat_close(callback: types.CallbackQuery, state: FSMContext):
    try:
        conv_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("❌ معرف غير صالح")
        return
    session = SessionLocal()
    try:
        conv = session.query(ChatConversation).filter(ChatConversation.id == conv_id).first()
        if conv:
            conv.is_closed = True
            session.commit()
    finally:
        session.close()
    await callback.answer("تم الإغلاق")
    await admin_chat_inbox(callback, state)

async def admin_chat_reply(message: types.Message, state: FSMContext):
    body = (message.text or "").strip()
    if not body:
        return
    data = await state.get_data()
    conv_id = data.get("admin_chat_conv_id")
    if not conv_id:
        await message.answer("❌ لا توجد محادثة محددة.", reply_markup=get_admin_main_kb())
        await state.clear()
        return

    session = SessionLocal()
    try:
        conv = session.query(ChatConversation).filter(ChatConversation.id == int(conv_id)).first()
        if not conv or conv.is_closed:
            await message.answer("❌ المحادثة غير متاحة.", reply_markup=get_admin_main_kb())
            await state.clear()
            return
        msg = ChatMessage(conversation_id=conv.id, sender_role="admin", sender_user_id=None, body=body)
        session.add(msg)
        conv.updated_at = datetime.datetime.utcnow()
        session.commit()
        target_id = conv.user_a_id
    finally:
        session.close()

    try:
        await message.bot.send_message(int(target_id), f"💬 رسالة من الإدارة:\n{body}")
    except Exception:
        pass
    await message.answer("✅ تم إرسال الرد.", reply_markup=get_admin_main_kb())
    await state.clear()

# --- التسجيل النهائي للمعالجات ---
def register_admin_handlers(dp: Dispatcher):
    # الدخول والخروج
    dp.message.register(admin_access_handler, Command("admin"))
    dp.callback_query.register(back_to_main_menu, F.data == "back_to_main")
    dp.callback_query.register(admin_open_from_button, F.data == "admin:open_panel")

    # إضافة اللعبة
    dp.callback_query.register(start_add_game, F.data == "admin:add_game")
    dp.message.register(process_game_name, AddGameFlow.waiting_for_name)
    dp.message.register(process_package, AddGameFlow.waiting_for_package)
    dp.callback_query.register(process_provider, AddGameFlow.waiting_for_provider)
    dp.message.register(process_key_step, AddGameFlow.waiting_for_key)
    dp.message.register(process_advanced_and_save, AddGameFlow.waiting_for_advanced_data)

    # الخطط الزمنية
    dp.callback_query.register(start_manage_timelines, F.data.startswith("admin:manage_timelines"))
    dp.callback_query.register(process_timeline_edit, F.data.startswith("admin:edit_tl:"))
    dp.message.register(save_timeline_data, AdminStates.waiting_for_timeline_data)

    # المالية والإحصائيات
    dp.callback_query.register(open_finance_menu, F.data == "admin:finance_menu")
    dp.callback_query.register(open_store_admin_menu, F.data == "admin:store_menu")
    dp.callback_query.register(store_inbox, F.data.startswith("admin:store_inbox"))
    dp.callback_query.register(store_published, F.data.startswith("admin:store_published"))
    dp.callback_query.register(store_item_view, F.data.startswith("admin:store_item:"))
    dp.callback_query.register(store_item_view, F.data.startswith("admin:store_item_pub:"))
    dp.callback_query.register(store_item_preview, F.data.startswith("admin:store_preview:"))
    dp.callback_query.register(store_item_toggle, F.data.startswith("admin:store_toggle:"))
    dp.callback_query.register(store_item_toggle, F.data.startswith("admin:store_toggle_pub:"))
    dp.callback_query.register(store_choose_category, F.data.startswith("admin:store_choose_cat:"))
    dp.callback_query.register(store_set_category, F.data.startswith("admin:store_set_cat:"))
    dp.callback_query.register(store_start_set_title, F.data.startswith("admin:store_set_title:"))
    dp.callback_query.register(store_start_set_price, F.data.startswith("admin:store_set_price:"))
    dp.callback_query.register(store_start_set_desc, F.data.startswith("admin:store_set_desc:"))
    dp.message.register(store_set_title, AdminStates.waiting_for_store_item_title)
    dp.message.register(store_set_price, AdminStates.waiting_for_store_item_price)
    dp.message.register(store_set_desc, AdminStates.waiting_for_store_item_desc)
    dp.callback_query.register(store_delete_confirm, F.data.startswith("admin:store_delete_confirm:"))
    dp.callback_query.register(store_delete_do, F.data.startswith("admin:store_delete:"))
    dp.callback_query.register(store_categories_menu, F.data == "admin:store_categories")
    dp.callback_query.register(store_category_toggle, F.data.startswith("admin:store_cat_toggle:"))
    dp.callback_query.register(store_category_add_start, F.data == "admin:store_cat_add")
    dp.message.register(store_category_add, AdminStates.waiting_for_store_category_name)
    dp.callback_query.register(show_admin_stats, F.data == "admin:finance_stats")
    dp.callback_query.register(reset_wallets_handler, F.data == "admin:reset_wallets_auth")
    dp.callback_query.register(reset_currency_handler, F.data == "admin:reset_currency_auth")
    dp.callback_query.register(start_add_balance, F.data == "admin:add_balance")
    dp.message.register(process_user_id, AdminStates.waiting_for_user_id)
    dp.message.register(final_add_balance, AdminStates.waiting_for_amount)
    dp.callback_query.register(start_gen_coupon, F.data == "admin:gen_coupon")
    dp.message.register(process_gen_coupon, AdminStates.waiting_for_coupon_amount)
    dp.callback_query.register(start_set_price, F.data == "admin:set_price")
    dp.callback_query.register(start_set_price, F.data == "admin:set_request_price")
    dp.message.register(final_set_price, AdminStates.waiting_for_op_price)
    dp.callback_query.register(start_broadcast, F.data == "admin:broadcast")
    dp.message.register(final_broadcast, AdminStates.waiting_for_broadcast_msg)
    dp.callback_query.register(view_all_users, F.data == "admin:view_users")
    
    # النسخ الاحتياطي
    dp.callback_query.register(trigger_manual_backup, F.data == "admin:backup_now")

    dp.callback_query.register(admin_chat_inbox, F.data == "admin:chat_inbox")
    dp.callback_query.register(admin_chat_open, F.data.startswith("admin:chat_open:"))
    dp.callback_query.register(admin_chat_close, F.data.startswith("admin:chat_close:"))
    dp.message.register(admin_chat_reply, AdminStates.waiting_for_chat_reply)
