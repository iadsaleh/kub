import os
import zipfile
import asyncio
from datetime import datetime
from aiogram.types import FSInputFile

async def create_project_backup():
    """
    يقوم بضغط ملفات المشروع وقاعدة البيانات في ملف ZIP
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    zip_filename = f"KUN_Backup_{timestamp}.zip"
    
    # قائمة المجلدات/الملفات التي نتجاهلها
    exclude_dirs = {'__pycache__', '.git', 'venv', '.idea', 'cache'}
    exclude_extensions = {'.pyc', '.log', '.zip'}

    try:
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # نسير في المجلد الحالي (root directory)
            # بما أننا داخل modules، نعود خطوة للخلف
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
            for root, dirs, files in os.walk(root_dir):
                # تنظيف المجلدات المستثناة
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                
                for file in files:
                    # تجاوز ملفات الـ zip والملفات المؤقتة
                    if file == zip_filename or any(file.endswith(ext) for ext in exclude_extensions):
                        continue
                        
                    file_path = os.path.join(root, file)
                    # اسم الملف داخل الأرشيف (Relative Path)
                    arcname = os.path.relpath(file_path, root_dir)
                    zipf.write(file_path, arcname)
                    
        return zip_filename
    except Exception as e:
        print(f"Backup Error: {e}")
        return None

async def send_backup_to_admin(bot, admin_id):
    """
    ينشئ النسخة ويرسلها للمدير ثم يحذف الملف المؤقت
    """
    zip_path = await create_project_backup()
    
    if zip_path and os.path.exists(zip_path):
        try:
            caption = (
                f"📦 **نسخة احتياطية شاملة**\n"
                f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"🗂️ المحتوى: قاعدة البيانات + الكود المصدري"
            )
            # إرسال الملف
            await bot.send_document(
                chat_id=admin_id, 
                document=FSInputFile(zip_path), 
                caption=caption
            )
        except Exception as e:
            print(f"Failed to send backup: {e}")
        finally:
            # حذف الملف بعد الإرسال لتوفير المساحة
            os.remove(zip_path)
    else:
        print("Could not create backup file.")
