import json
import os
import sys

# ضمان إمكانية استيراد قاعدة البيانات من المديولات
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.database import SessionLocal, Game, init_db, engine, Base

def migrate_config_to_db():
    print("🚀 بدء عملية نقل الألعاب من config.json إلى قاعدة البيانات...")
    
    # 1. إنشاء الجداول إذا لم تكن موجودة
    Base.metadata.create_all(bind=engine)
    
    # 2. البحث عن ملف الكونفيغ
    config_path = "config.json"
    if not os.path.exists(config_path):
        # محاولة البحث في المسار السابق إذا تم تغيير الهيكلية
        config_path = "../config.json"
        if not os.path.exists(config_path):
            print("❌ ملف config.json غير موجود! تأكد من وضعه بجانب هذا السكريبت.")
            return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ خطأ في قراءة الملف: {e}")
        return
    
    games_dict = data.get("games", {})
    if not games_dict:
        print("⚠️ الملف لا يحتوي على ألعاب (مفتاح 'games' فارغ).")
        return

    session = SessionLocal()
    added_count = 0
    updated_count = 0

    print(f"📦 تم العثور على {len(games_dict)} لعبة. جاري المعالجة...")

    for alias, g_data in games_dict.items():
        # استخراج البيانات
        # إذا كان الاسم غير موجود، نستخدم الـ Alias كاسم مؤقت
        name = g_data.get("name", alias.replace("games_", "").replace("_", " ").title())
        
        # محاولة استنتاج المزود إذا لم يكن موجوداً
        provider = g_data.get("provider")
        if not provider:
            if "app_token" in g_data: provider = "Adjust"
            elif "dev_key" in g_data: provider = "AppsFlyer"
            else: provider = "AppsFlyer"

        # البيانات الكاملة تخزن في json_data
        json_payload = g_data.copy()
        
        # التحقق هل اللعبة موجودة مسبقاً؟
        existing_game = session.query(Game).filter(Game.alias == alias).first()
        
        if existing_game:
            existing_game.name = name
            existing_game.provider = provider
            existing_game.json_data = json_payload
            updated_count += 1
        else:
            new_game = Game(
                alias=alias,
                name=name,
                provider=provider,
                json_data=json_payload,
                is_active=True
            )
            session.add(new_game)
            added_count += 1
            
    session.commit()
    session.close()
    
    print("-" * 30)
    print(f"✅ تمت العملية بنجاح!")
    print(f"📥 تمت إضافة: {added_count} لعبة")
    print(f"🔄 تم تحديث: {updated_count} لعبة")
    print("-" * 30)

if __name__ == "__main__":
    migrate_config_to_db()
