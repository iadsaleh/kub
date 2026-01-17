import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from datetime import datetime, timedelta
from .database import db_log_history, SessionLocal, SystemSetting
from .s2s_engine import kun_engine
import json

# إعداد السجلات لمراقبة المهام المجدولة في الخلفية
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NexusScheduler")

async def proxy_check_wrapper():
    """Periodic task to check global proxies health"""
    logger.info("🛡️ Starting Proxy Health Check...")
    session = SessionLocal()
    try:
        setting = session.query(SystemSetting).filter(SystemSetting.key == "global_proxies").first()
        if not setting:
            return
            
        data = setting.value if isinstance(setting.value, dict) else {}
        proxies = data.get("proxies", [])
        
        # Normalize proxies list
        proxy_list = []
        if isinstance(proxies, list):
            for p in proxies:
                if isinstance(p, str):
                    proxy_list.append(p)
                elif isinstance(p, dict) and "url" in p:
                    proxy_list.append(p["url"])

        health_map = data.get("health", {})
        updates_count = 0

        for proxy_url in proxy_list:
            if not proxy_url: continue
            
            # Check proxy
            is_alive, ip, _ = await kun_engine.check_proxy(proxy_url)
            
            health_map[proxy_url] = {
                "alive": is_alive,
                "ip": ip if is_alive else None,
                "last_check": datetime.utcnow().isoformat(),
                "error": str(ip) if not is_alive else None
            }
            
            if not is_alive:
                logger.warning(f"⚠️ Proxy Down: {proxy_url} - Error: {ip}")
            else:
                logger.info(f"✅ Proxy Active: {proxy_url} - IP: {ip}")
            
            updates_count += 1
        
        if updates_count > 0:
            data["health"] = health_map
            setting.value = data
            session.commit()
            logger.info("💾 Proxy health stats updated in DB.")
            
    except Exception as e:
        logger.error(f"❌ Proxy Health Check Failed: {e}")
    finally:
        session.close()

# دالة وسيطة لضمان تنفيذ المهام الـ async وإظهارها في الواجهة
async def s2s_job_wrapper(func, game_alias, event_info, profile, user_id):
    """
    هذه الدالة هي التي تضمن ظهور السجلات في الواجهة.
    تقوم باستدعاء محرك الإرسال وتمرير النتيجة للطابور.
    """
    try:
        # استدعاء دالة الإرسال من المحرك
        # event_info قد يحتوي على {'name':..., 'token':..., 'value':...}
        status, resp, req_h, req_b, res_h, res_time = await func(game_alias, event_info, profile)
        
        db_log_history(
            user_id, game_alias, "Scheduled", event_info.get('name', 'Unknown'), status, resp,
            request_headers=req_h, request_body=req_b, response_headers=res_h, response_time_ms=res_time
        )
        
        logger.info(f"✅ Executed Scheduled Job: {game_alias} - Status: {status}")
    except Exception as e:
        logger.error(f"❌ Error in Scheduled Job: {e}")

class NexusScheduler:
    def __init__(self):
        # استخدام SQLite لضمان بقاء المهام حتى بعد إعادة تشغيل Termux
        jobstores = {
            'default': SQLAlchemyJobStore(url='sqlite:///nexus_jobs.db')
        }
        
        # إعدادات متقدمة للتعامل مع تأخير المهام (Misfire)
        job_defaults = {
            'coalesce': True, # دمج المهام المتراكمة
            'max_instances': 20, # رفع العدد لدعم العمليات المكثفة
            'misfire_grace_time': 86400 # مهلة يوم كامل (24 ساعة) لتنفيذ المهام المتأخرة
        }

        self.scheduler = AsyncIOScheduler(jobstores=jobstores, job_defaults=job_defaults)

    def start(self):
        """بدء تشغيل المجدول"""
        if not self.scheduler.running:
            self.scheduler.add_job(proxy_check_wrapper, 'interval', minutes=15, id='proxy_checker', replace_existing=True)
            self.scheduler.start()
            logger.info("🚀 Nexus Scheduler started successfully.")

    def add_s2s_job(self, func, run_date, args):
        """
        إضافة مهمة إرسال حدث S2S.
        تمت إزالة misfire_instruction لتجنب الخطأ في بعض نسخ المكتبة
        والاعتماد على misfire_grace_time المعرفة في الأعلى.
        """
        try:
            # args هنا هي: (game_alias, event_info, profile)
            job = self.scheduler.add_job(
                s2s_job_wrapper, 
                'date', 
                run_date=run_date, 
                args=[func] + list(args)
            )
            logger.info(f"📅 Job scheduled: {job.id} at {run_date}")
            return job
        except Exception as e:
            logger.error(f"❌ Error while adding job to scheduler: {e}")
            raise e

    def schedule_farm_sequence(self, func, start_time, gap_seconds, levels, args_base):
        """جدولة مزرعة تقليدية"""
        current_time = start_time
        for lvl in levels:
            # args_base: [game_alias, profile, user_id]
            event_info = {"name": "level_up", "level": lvl, "token": None}
            self.add_s2s_job(func, current_time, (args_base[0], event_info, args_base[1], args_base[2]))
            current_time += timedelta(seconds=gap_seconds)

    def schedule_natural_path(self, func, start_time, timelines, args_base):
        """جدولة المسار الطبيعي (أيام وساعات)"""
        for step in timelines:
            execution_time = start_time + timedelta(
                days=step.day_offset, 
                hours=step.hour_offset, 
                minutes=step.minute_offset
            )
            
            event_info = {
                "name": step.step_name,
                "token": step.event_token,
                "value": step.event_value,
                "level": step.event_value
            }
            
            # args_base: [game_alias, profile, user_id]
            self.add_s2s_job(func, execution_time, (args_base[0], event_info, args_base[1], args_base[2]))
            logger.info(f"🎭 Natural Step Queued: {step.step_name} for {execution_time}")

    def schedule_custom_plan(self, func, start_time, steps_json, args_base):
        """جدولة خطة المستخدم اليدوية"""
        for step in steps_json:
            # الحساب مطلق من وقت البداية
            execution_time = start_time + timedelta(hours=step.get('delay_hours', 0))
            
            event_info = {
                "name": step.get('step', 'CustomStep'),
                "level": step.get('step'),
                "token": step.get('token'),
                "value": step.get('value')
            }
            
            # args_base: [game_alias, profile, user_id]
            self.add_s2s_job(func, execution_time, (args_base[0], event_info, args_base[1], args_base[2]))

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown()

# كائن موحد للمشروع
nexus_scheduler = NexusScheduler()
