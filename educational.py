from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

# لوحة مفاتيح القسم التعليمي
def get_edu_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📱 دليل استخراج المعرفات", callback_data="edu:ids"))
    builder.row(types.InlineKeyboardButton(text="📝 صيغة الإرسال (أمثلة)", callback_data="edu:format"))
    builder.row(types.InlineKeyboardButton(text="🛡️ معايير الأمان (UA/IP)", callback_data="edu:security"))
    builder.row(types.InlineKeyboardButton(text="📊 سجل العمليات الأخير", callback_data="my_history"))
    builder.row(types.InlineKeyboardButton(text="🏠 العودة للرئيسية", callback_data="back_to_main"))
    return builder.as_markup()

# معالج عرض القائمة التعليمية الرئيسية
async def show_edu_main(callback: types.CallbackQuery):
    text = (
        "📚 **الأكاديمية التقنية لـ KUN 0X Nexus**\n"
        "━━━━━━━━━━━━━━━\n"
        "مرحباً بك في القسم التعليمي.\n"
        "لضمان نجاح عملياتك بنسبة 100%، يجب عليك تزويد البوت بمعرفات حقيقية ودقيقة.\n\n"
        "هنا ستتعلم كيفية استخراج هذه المعرفات من جهازك بسهولة."
    )
    await callback.message.edit_text(text, reply_markup=get_edu_menu_kb())

# معالج شرح المعرفات
async def show_edu_ids(callback: types.CallbackQuery):
    text = (
        "🛠️ **دليل استخراج المعرفات (خطوة بخطوة):**\n\n"
        "يحتاج البوت إلى 4 معرفات ليعمل بكفاءة، ويجب إرسالها بالترتيب المحدد:\n\n"
        "1️⃣ **GAID (Google Advertising ID):**\n"
        "• **الطريقة:** الضبط ⚙️ > Google > الإعلانات (Ads) > انسخ 'معرفك الإعلاني'.\n"
        "• **الفائدة:** هو هويتك الإعلانية الأساسية.\n\n"
        "2️⃣ **af_id / ad_id (المعرف الخاص بالمنصة):**\n"
        "• **AppsFlyer (af_id):**\n"
        "   - **بدون روت:** راقب الروابط (HTTP Canary) وابحث عن `appsflyer_id`.\n"
        "   - **مع روت (Root):** افتح المسار:\n   `/data/data/اسم_حزمة_اللعبة/shared_prefs/appsflyer-data.xml`\n\n"
        "• **Adjust (ad_id):** هو كود `adid` الطويل (32 خانة) الخاص بتتبع الجهاز.\n\n"
        "3️⃣ **Android ID (AID):**\n"
        "• **الطريقة:** حمل تطبيق **Device ID** من المتجر. انسخ الرمز المكون من 16 خانة.\n\n"
        "4️⃣ **User Agent (UA):**\n"
        "• **الطريقة:** ابحث في جوجل عن **My User Agent** وانسخ النص الكامل.\n"
    )
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⬅️ عودة للتعليمات", callback_data="edu_main"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

async def show_edu_format(callback: types.CallbackQuery):
    text = (
        "📝 **الصيغة الصحيحة لإرسال البيانات:**\n\n"
        "عندما يطلب البوت المعرفات، يجب إرسالها **بالترتيب** في سطر واحد مفصولة بعلامة `|`.\n\n"
        "⚠️ **الترتيب الإلزامي:**\n"
        "`GAID | af_id (أو ad_id) | AID | UA`\n\n"
        "🚀 **أمثلة (نسخ ولصق مع التعديل):**\n\n"
        "**1️⃣ لمنصة AppsFlyer (استخدم af_id):**\n"
        "`38400000-8cf0-11bd-b23e-10b96e4ef00d|1699999999999-9999999|abcdef1234567890|Dalvik/2.1.0 (Linux; U; Android 10; SM-G960F)`\n\n"
        "**2️⃣ لمنصة Adjust (استخدم ad_id):**\n"
        "`38400000-8cf0-11bd-b23e-10b96e4ef00d|99999999999999999999999999999999|abcdef1234567890|Dalvik/2.1.0 (Linux; U; Android 10; SM-G960F)`\n\n"
        "📌 **تذكر:** لا تخلط الترتيب، ولا ترسل المعرفات بشكل عشوائي."
    )
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⬅️ عودة للتعليمات", callback_data="edu_main"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

async def show_edu_security(callback: types.CallbackQuery):
    text = (
        "🛡️ **معايير الأمان والحماية:**\n\n"
        "للحفاظ على حسابك وتجنب الحظر، يلتزم النظام بالمعايير التالية:\n\n"
        "✅ **محاكاة كاملة:** نستخدم User Agent حقيقي ليظهر الطلب وكأنه من هاتف فعلي.\n"
        "✅ **IP نظيف:** عند استخدام خوادمنا، نضمن استخدام عناوين IP عالية الجودة.\n"
        "✅ **تطابق البيانات:** يتم ربط الـ GAID مع الـ Android ID لضمان عدم وجود تضارب في البيانات."
    )
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⬅️ عودة للتعليمات", callback_data="edu_main"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

# دالة لربط كافة المعالجات بالديسباتشر الأساسي
def register_edu_handlers(dp):
    dp.callback_query.register(show_edu_main, F.data == "edu_main") 
    dp.callback_query.register(show_edu_ids, F.data == "edu:ids")
    dp.callback_query.register(show_edu_format, F.data == "edu:format")
    dp.callback_query.register(show_edu_security, F.data == "edu:security")
