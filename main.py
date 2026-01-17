import asyncio
import os
import signal
import sys
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot # استيراد Bot لإنشاء نسخ للبوتات الإضافية

# استيراد setup_bot_commands من bot_client لضمان ظهور زر المنيو
# نقوم باستيراد bot وتسميته primary_bot ليكون هو البوت الأساسي
from modules.bot_client import dp, bot as primary_bot, set_log_queue, setup_bot_commands
from modules.s2s_engine import set_engine_log_queue 
from modules.scheduler import nexus_scheduler
from modules.database import init_db, SessionLocal, BotToken
from modules.security import decrypt_token
from admin_tui import NexusAdmin
import threading
import uvicorn
from web_dashboard.app import app as web_app, log_injector

# استيراد وظيفة النسخ الاحتياطي (الجديدة)
try:
    from modules.backup_utils import send_backup_to_admin
except ImportError:
    send_backup_to_admin = None

# تحميل متغيرات البيئة (لضمان قراءة ADMIN_ID و BOT_TOKENS)
load_dotenv()

# طابور السجلات العالمي (The Central Nervous System)
log_queue = asyncio.Queue()

def kill_duplicate_instances():
    """قتل أي نسخة قديمة من السيرفر لضمان عدم حدوث Conflict (يدعم Windows & Linux)"""
    if str(os.getenv("KUN_KILL_DUPLICATES", "1")).strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        import psutil
    except Exception:
        return
    current_pid = os.getpid()
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
                
            cmdline = proc.info['cmdline']
            if cmdline and 'python' in proc.info['name'].lower():
                # التحقق مما إذا كان الأمر يشغل main.py
                if any('main.py' in arg for arg in cmdline):
                    print(f"⚠️ Killing duplicate instance: PID {proc.info['pid']}")
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

async def log_worker(app):
    """مراقبة الطابور وتحديث واجهة TUI لحظياً وبثها للويب"""
    while True:
        message = await log_queue.get()
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            if app is not None:
                try:
                    log_widget = app.query_one("#logs")
                    log_widget.write(f"║[{timestamp}] {message}")
                except Exception:
                    pass
            else:
                try:
                    print(f"[{timestamp}] {message}")
                except Exception:
                    pass
            await log_injector(message)
        except Exception:
            pass
        finally:
            log_queue.task_done()

def run_web_server():
    """تشغيل خادم الويب في Thread منفصل"""
    host = str(os.getenv("DASH_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    try:
        port = int(str(os.getenv("DASH_PORT", "8000")).strip() or "8000")
    except Exception:
        port = 8000
    uvicorn.run(web_app, host=host, port=port, log_level="error")

async def start_nexus(app=None, await_polling: bool = False):
    try:
        init_db()
        await log_queue.put("[bold blue]📦 Database Initialized & Connected.[/]")
    except Exception as e:
        await log_queue.put(f"[bold red]❌ DB Error:[/] {e}")

    set_log_queue(log_queue)
    set_engine_log_queue(log_queue)

    tokens_str = os.getenv("BOT_TOKENS", "")
    tokens_list = [t.strip() for t in tokens_str.split(',') if t.strip()]

    try:
        db = SessionLocal()
        db_tokens = db.query(BotToken).filter(BotToken.is_active == True).all()
        count_db = 0
        for t in db_tokens:
            try:
                decrypted = decrypt_token(t.token)
                if decrypted and decrypted not in tokens_list:
                    tokens_list.append(decrypted)
                    count_db += 1
            except Exception as e:
                await log_queue.put(f"[red]⚠️ Failed to decrypt token ID {t.id}: {e}[/]")
        db.close()
        if count_db > 0:
            await log_queue.put(f"[blue]📥 Loaded {count_db} extra bots from Database.[/]")
    except Exception as e:
        await log_queue.put(f"[red]⚠️ DB Token Fetch Error: {e}[/]")

    active_bots = []

    if not tokens_list:
        active_bots.append(primary_bot)
        await primary_bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(primary_bot)
        await log_queue.put("[yellow]⚠️ No BOT_TOKENS list found. Running single bot mode.[/]")
    else:
        await log_queue.put(f"[bold cyan]🤖 Initializing {len(tokens_list)} Bots...[/]")
        for i, token in enumerate(tokens_list):
            try:
                if i == 0 and token == primary_bot.token:
                    current_bot = primary_bot
                else:
                    current_bot = Bot(token=token)

                await current_bot.delete_webhook(drop_pending_updates=True)
                await setup_bot_commands(current_bot)
                active_bots.append(current_bot)

                try:
                    me = await current_bot.get_me()
                    await log_queue.put(f"[green]✅ Bot #{i+1} Connected: @{me.username}[/]")
                except Exception:
                    await log_queue.put(f"[green]✅ Bot #{i+1} Connected (Token ok).[/]")
            except Exception as e:
                await log_queue.put(f"[bold red]❌ Failed to connect Bot #{i+1}: {e}[/]")

    await log_queue.put("[bold green]✅ Telegram Session & Commands Initialized.[/]")

    try:
        nexus_scheduler.start()
        await log_queue.put("[bold magenta]⏰ Scheduler Engine Active.[/]")
    except Exception as e:
        await log_queue.put(f"[red]⚠️ Scheduler start failed: {e}[/]")

    asyncio.create_task(log_worker(app))

    threading.Thread(target=run_web_server, daemon=True).start()
    await log_queue.put(f"[bold cyan]🌐 Web Dashboard running at http://localhost:{int(str(os.getenv('DASH_PORT', '8000')).strip() or '8000')}[/]")

    if active_bots:
        asyncio.create_task(scheduled_backup_task(active_bots[0]))

    if not active_bots:
        await log_queue.put("[bold red]❌ No bots active! Check your .env file.[/]")
        return

    if await_polling:
        await log_queue.put(f"[bold gold1]🚀 KUN 0X NEXUS Online ({len(active_bots)} Bots) - Waiting for commands...[/]")
        await dp.start_polling(*active_bots, handle_signals=False)
    else:
        asyncio.create_task(dp.start_polling(*active_bots, handle_signals=False))
        await log_queue.put(f"[bold gold1]🚀 KUN 0X NEXUS Online ({len(active_bots)} Bots) - Waiting for commands...[/]")

# --- مهمة النسخ الاحتياطي التلقائي (Auto Backup Task) ---
async def scheduled_backup_task(bot_instance):
    """مهمة خلفية لعمل نسخ احتياطي كل 12 ساعة"""
    if not send_backup_to_admin:
        await log_queue.put("[yellow]⚠️ Backup module not found. Auto-backup disabled.[/]")
        return

    await log_queue.put("[blue]⏳ Auto-backup scheduler started (Every 12h).[/]")
    while True:
        # الانتظار 12 ساعة (43200 ثانية)
        await asyncio.sleep(12 * 60 * 60) 
        
        admin_id = os.getenv("ADMIN_ID")
        if admin_id:
            try:
                # نرسل النسخة باستخدام البوت الذي تم تمريره (عادة الأول)
                await send_backup_to_admin(bot_instance, int(admin_id))
                await log_queue.put("[green]✅ Auto-backup sent successfully via Primary Bot.[/]")
            except Exception as e:
                await log_queue.put(f"[red]⚠️ Auto-backup failed: {e}[/]")

class NexusManager(NexusAdmin):
    """إدارة النظام الكلية من داخل واجهة Textual الاحترافية"""
    
    async def on_mount(self) -> None:
        await start_nexus(self, await_polling=False)

    async def on_unmount(self) -> None:
        """إغلاق نظيف وآمن لكافة الاتصالات عند الخروج"""
        try:
            nexus_scheduler.shutdown()
            # إغلاق جلسة البوت الأساسي
            await primary_bot.session.close()
        except:
            pass

if __name__ == "__main__":
    # تنظيف العمليات المكررة في Termux
    kill_duplicate_instances()

    headless = str(os.getenv("KUN_HEADLESS", "")).strip().lower() in ("1", "true", "yes", "on")
    if not sys.stdout.isatty():
        headless = True

    if headless:
        try:
            asyncio.run(start_nexus(app=None, await_polling=True))
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"❌ Critical System Failure: {e}")
    else:
        app = NexusManager()
        try:
            app.run()
        except Exception as e:
            print(f"❌ Critical System Failure: {e}")
