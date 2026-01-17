import json
import os
import shutil
import re

CONFIG_FILE = 'config.json'
BACKUP_FILE = 'config_backup_smart.json'

# القائمة البيضاء: الحقول التي نريد الاحتفاظ بها (الثوابت فقط)
ALLOWED_KEYS = [
    "app_id",
    "dev_key",      # AppsFlyer Key
    "app_token",    # Adjust Key
    "start_level",
    "target_level",
    "padding",
    "event_templates",
    "level_sequence"
]

def clean_game_name(raw_key):
    """
    تنظيف الاسم من الزوائد والكلمات غير المرغوبة
    """
    name = raw_key.lower()
    
    # قائمة الكلمات المحذوفة
    words_to_remove = [
        'imported', 'games_', 'com_', 'net_', 'org_', 'app_',
        'burny_', 'playrix_', 'king_', 'supercell_',
        'unity_', 'real_'
    ]

    for word in words_to_remove:
        name = name.replace(word, ' ')

    name = name.replace('_', ' ').replace('.', ' ').replace('-', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    return name.title()

def organize_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ الملف {CONFIG_FILE} غير موجود!")
        return

    # نسخة احتياطية
    shutil.copy(CONFIG_FILE, BACKUP_FILE)
    print(f"✅ تم حفظ نسخة احتياطية: {BACKUP_FILE}")

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ خطأ في قراءة JSON: {e}")
        return

    original_games = data.get('games', {})
    new_games = {}

    print("🔄 جاري تحليل وتصنيف الألعاب...")

    for old_key, game_data in original_games.items():
        # 1. تنظيف الاسم
        readable_name = clean_game_name(old_key)
        clean_alias = readable_name.lower().replace(' ', '_')

        if "app_id" not in game_data:
            continue

        # --- 2. المنطق الذكي لتحديد المزود (Provider Logic) ---
        provider = "Unknown"
        
        # الأولوية للبحث عن المفاتيح المميزة لكل منصة
        if "app_token" in game_data:
            provider = "Adjust"
        elif "dev_key" in game_data:
            provider = "AppsFlyer"
        # يمكن إضافة Singular مستقبلاً (مثلاً if 'api_key'...)
        
        # طباعة التقرير للمستخدم
        print(f"   🎮 {readable_name}")
        print(f"      ├─ ID: {clean_alias}")
        print(f"      └─ Provider: {provider} (Detected)")

        # بناء الكائن الجديد
        new_game_obj = {
            "name": readable_name,
            "alias": clean_alias,
            "provider": provider  # <--- المزود المكتشف تلقائياً
        }

        # نقل البيانات المسموحة فقط
        for key, value in game_data.items():
            if key in ALLOWED_KEYS:
                new_game_obj[key] = value

        new_games[clean_alias] = new_game_obj

    data['games'] = new_games

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("-" * 30)
    print(f"✅ تم التصنيف بنجاح!")
    print(f"📂 الملف جاهز: {CONFIG_FILE}")

if __name__ == "__main__":
    organize_config()
