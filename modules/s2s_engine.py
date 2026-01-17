import httpx
from httpx_socks import AsyncProxyTransport
import json
import time
import random
import re
import asyncio
import base64
import uuid
from datetime import datetime, timedelta

def _default_ua(device_os: str | None) -> str:
    os_name = (device_os or "android").strip().lower()
    if os_name == "ios":
        return "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    return "Dalvik/2.1.0 (Linux; U; Android 13)"

# استيراد قاعدة البيانات
try:
    from database import SessionLocal, Game
except ImportError:
    from .database import SessionLocal, Game

# استيراد طابور السجلات
try:
    from main import log_queue
except ImportError:
    log_queue = None

class KUNNexusEngine:
    def __init__(self):
        pass

    async def send_log(self, message: str):
        """إرسال السجلات لواجهة المدير TUI"""
        global log_queue
        if log_queue:
            try:
                await log_queue.put(message)
            except:
                pass

    async def check_proxy(self, proxy_url: str):
        """تحقق من صلاحية البروكسي وجلب الآيبي الخارجي"""
        async_client_kwargs = {"timeout": 10.0, "verify": False}
        transport = None
        if proxy_url:
            if proxy_url.startswith("socks"):
                transport = AsyncProxyTransport.from_url(proxy_url)
            else:
                async_client_kwargs["proxy"] = proxy_url
        
        try:
            async with httpx.AsyncClient(transport=transport, **async_client_kwargs) as client:
                r = await client.get("https://api.ipify.org?format=json")
                r.raise_for_status()
                data = r.json()
                ip = data.get("ip")
                
                # Optional: Get location info if needed, but ipify is just IP.
                # Could use ip-api.com for location but might be rate limited.
                return True, ip, {}
        except Exception as e:
            return False, str(e), {}

    # --- منطق حساب الانتظار الذكي ---
    def calculate_smart_sleep(self, level_str, platform):
        try:
            num = int(re.search(r'\d+', str(level_str)).group())
        except: 
            num = 1
        
        if platform == "adjust":
            base = 45 + (num * 10)
            if base > 900: base = 900
        else: # AppsFlyer / Singular
            base = 45 + (num * 12)
            if base > 1200: base = 1200
            
        jitter = int(base * 0.2)
        return max(30, base + random.randint(-jitter, jitter))

    # =========================================================
    #  1. وضع السيرفر (Server Mode) - الإرسال المباشر
    # =========================================================
    async def send_event(self, game_key, event_data, user_profile):
        session = SessionLocal()
        try:
            game_obj = session.query(Game).filter(Game.alias == game_key).first()
            
            if not game_obj: 
                return 404, "Game Key Missing in DB", {}, "", {}, 0.0
            
            if not game_obj.is_active:
                return 403, "Game is disabled", {}, "", {}, 0.0

            game_config = game_obj.json_data.copy() if game_obj.json_data else {}
            game_config['provider'] = game_obj.provider
            
        finally:
            session.close()
        
        raw_platform = game_config.get("provider") or game_config.get("platform") or "appsflyer"
        platform = raw_platform.lower()

        proxy_url = None
        if isinstance(user_profile, dict):
            proxy_url = user_profile.get("proxy_url") or None

        async_client_kwargs = {"http2": True, "verify": False, "timeout": 30.0}
        transport = None
        if proxy_url:
            if proxy_url.startswith("socks"):
                transport = AsyncProxyTransport.from_url(proxy_url)
            else:
                async_client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(transport=transport, **async_client_kwargs) as client:
            out_ip = None
            try:
                r = await client.get("https://api.ipify.org?format=json", timeout=8.0)
                if r.status_code == 200:
                    out_ip = (r.json() or {}).get("ip")
            except Exception:
                out_ip = None

            if "appsflyer" in platform:
                status, resp, req_h, req_b, res_h, res_time = await self._fire_appsflyer(client, game_config, event_data, user_profile)
            elif "adjust" in platform:
                status, resp, req_h, req_b, res_h, res_time = await self._fire_adjust(client, game_config, event_data, user_profile)
            elif "singular" in platform:
                status, resp, req_h, req_b, res_h, res_time = await self._fire_singular(client, game_config, event_data, user_profile)
            else:
                return 400, f"Platform {platform} not implemented", {}, "", {}, 0.0

            res_h = dict(res_h or {})
            if out_ip:
                res_h["out_ip"] = out_ip
            return status, resp, req_h, req_b, res_h, res_time

    # =========================================================
    #  2. وضع العميل (Client Mode) - توليد الروابط والأكواد
    # =========================================================
    async def generate_client_mission(self, game_key, event_data, user_profile):
        """
        تجهيز المهمة للتنفيذ من جانب العميل:
        - Adjust: تعيد رابط مباشر.
        - AppsFlyer: تعيد كود مشفر للأداة.
        """
        session = SessionLocal()
        try:
            game_obj = session.query(Game).filter(Game.alias == game_key).first()
            if not game_obj: return None, "Game not found"
            
            game_config = game_obj.json_data.copy() if game_obj.json_data else {}
            game_config['provider'] = game_obj.provider
        finally:
            session.close()

        raw_platform = game_config.get("provider") or "appsflyer"
        platform = raw_platform.lower()

        # --- A. ألعاب Adjust (رابط مباشر) ---
        if "adjust" in platform:
            base_url = "https://app.adjust.com/event"
            created_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + "Z"
            device_os = (user_profile.get("device_os") or "android").lower()
            gps_adid = user_profile.get("gaid") or user_profile.get("adjust_id")
            idfa = user_profile.get("idfa")
            
            params = [
                f"app_token={game_config.get('app_token')}",
                f"event_token={event_data.get('token') or event_data.get('name')}",
                f"created_at={created_at}",
                f"environment={game_config.get('environment', 'production')}"
            ]

            if device_os == "ios":
                if idfa:
                    params.append(f"idfa={idfa}")
                if user_profile.get("idfv"):
                    params.append(f"idfv={user_profile.get('idfv')}")
            else:
                params.append(f"gps_adid={gps_adid}")
            
            if user_profile.get("android_id"):
                params.append(f"android_id={user_profile.get('android_id')}")
            
            if user_profile.get("ip"):
                params.append(f"ip_address={user_profile.get('ip')}")

            final_link = f"{base_url}?" + "&".join(params)
            return "LINK", final_link

        # --- B. ألعاب AppsFlyer (كود Launcher) ---
        elif "appsflyer" in platform:
            url = f"https://api2.appsflyer.com/inappevent/{game_config['app_id']}"
            
            # محاكاة نفس منطق بناء البيانات في _fire_appsflyer
            processing_lag_ms = random.randint(500, 2500)
            simulated_time = datetime.utcnow() - timedelta(milliseconds=processing_lag_ms)
            event_time = simulated_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            lvl = event_data.get('level', '1')
            padding = game_config.get('padding', 0)
            lvl_str = str(lvl).zfill(padding) if padding > 0 else str(lvl)

            templates = game_config.get('event_templates', {})
            level_template = templates.get('level_up')
            
            if level_template:
                event_name = level_template['event_name'].replace("{LEVEL}", lvl_str)
                event_value = level_template['json_template'].replace("{LEVEL}", lvl_str)
                if "{GAID}" in event_value:
                    event_value = event_value.replace("{GAID}", user_profile.get("gaid", ""))
                if "{IDFA}" in event_value:
                    event_value = event_value.replace("{IDFA}", user_profile.get("idfa", ""))
            else:
                event_name = str(event_data.get('name', "level_completed")).replace("{LEVEL}", lvl_str)
                event_value = json.dumps({"level": lvl_str})

            af_id = user_profile.get("af_id") or user_profile.get("appsflyer_uid")
            if not af_id:
                # Generate random AppsFlyer ID (Timestamp-Random)
                af_id = f"{int(time.time() * 1000)}-{random.randint(1000000000000000000, 9223372036854775807)}"

            payload = {
                "eventName": event_name,
                "eventValue": event_value,
                "eventTime": event_time,
                "af_events_api": "true"
            }
            if af_id: payload["appsflyer_id"] = af_id
            device_os = (user_profile.get("device_os") or "android").lower()
            if device_os == "ios":
                if user_profile.get("idfa"):
                    payload["idfa"] = user_profile.get("idfa")
            else:
                if user_profile.get("gaid"):
                    payload["advertising_id"] = user_profile.get("gaid")
                if user_profile.get("android_id"):
                    payload["android_id"] = user_profile.get("android_id")

            headers = {
                "authentication": game_config.get('dev_key'),
                "Content-Type": "application/json",
                "User-Agent": user_profile.get("ua") or _default_ua(user_profile.get("device_os")),
                "X-Appsflyer-App-ID": game_config.get('app_id')
            }

            mission_data = {
                "url": url,
                "method": "POST",
                "headers": headers,
                "payload": payload,
                "note": f"{game_obj.name} - Level {lvl}"
            }
            
            # تشفير البيانات
            json_str = json.dumps(mission_data)
            mission_code = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
            
            return "CODE", mission_code

        return None, "Unsupported Platform for Client Side"

    # =========================================================
    #  3. دوال التنفيذ الداخلي (Internal Firing Methods)
    # =========================================================

    async def _fire_appsflyer(self, client, game, event_data, user):
        url = f"https://api2.appsflyer.com/inappevent/{game['app_id']}"
        
        processing_lag_ms = random.randint(500, 2500)
        simulated_time = datetime.utcnow() - timedelta(milliseconds=processing_lag_ms)
        event_time = simulated_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        lvl = event_data.get('level', '1')
        padding = game.get('padding', 0)
        lvl_str = str(lvl).zfill(padding) if padding > 0 else str(lvl)

        templates = game.get('event_templates', {})
        level_template = templates.get('level_up')
        
        if level_template:
            event_name = level_template['event_name'].replace("{LEVEL}", lvl_str)
            event_value = level_template['json_template'].replace("{LEVEL}", lvl_str)
            if "{GAID}" in event_value:
                event_value = event_value.replace("{GAID}", user.get("gaid", ""))
            if "{IDFA}" in event_value:
                event_value = event_value.replace("{IDFA}", user.get("idfa", ""))
        else:
            event_name = str(event_data.get('name', "level_completed")).replace("{LEVEL}", lvl_str)
            event_value = json.dumps({"level": lvl_str})

        af_id = user.get("af_id") or user.get("appsflyer_uid")
        if not af_id:
             # Generate random AppsFlyer ID (Timestamp-Random)
             af_id = f"{int(time.time() * 1000)}-{random.randint(1000000000000000000, 9223372036854775807)}"
        
        payload = {
            "eventName": event_name,
            "eventValue": event_value,
            "eventTime": event_time,
            "af_events_api": "true"
        }
        if af_id: payload["appsflyer_id"] = af_id

        device_os = (user.get("device_os") or "android").lower()
        if device_os == "ios":
            if user.get("idfa"):
                payload["idfa"] = user.get("idfa")
        else:
            if user.get("gaid"):
                payload["advertising_id"] = user.get("gaid")
        
        android_id = user.get("android_id")
        if device_os != "ios" and android_id:
            payload["android_id"] = android_id
        
        headers_to_send = {
            "authentication": game.get('dev_key'),
            "Content-Type": "application/json",
            "User-Agent": user.get("ua") or _default_ua(device_os),
            "X-Appsflyer-App-ID": game.get('app_id'),
            "Connection": "Keep-Alive"
        }
        headers_for_log = dict(headers_to_send)
        if user.get("proxy_display"):
            headers_for_log["_nexus_proxy"] = user.get("proxy_display")
        
        try:
            await asyncio.sleep(random.uniform(0.1, 0.5))
            start_time = time.time()
            resp = await client.post(url, json=payload, headers=headers_to_send)
            response_time_ms = (time.time() - start_time) * 1000
            
            color = "green" if resp.status_code < 400 else "red"
            await self.send_log(f"[{color}]📡 AF Resp ({resp.status_code}):[/] {resp.text[:100]}")
            
            return resp.status_code, resp.text, headers_for_log, json.dumps(payload), dict(resp.headers), response_time_ms
        except Exception as e:
            return 500, str(e), headers_for_log, json.dumps(payload), {}, 0.0

    async def _fire_adjust(self, client, game, event_data, user):
        url = "https://app.adjust.com/event"
        created_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + "Z"
        
        device_os = (user.get("device_os") or "android").lower()
        gps_adid = user.get("gaid") or user.get("adjust_id")

        # Determine Event Token
        # Priority: 1. Token from Game Config (Mapped by Name) 2. Token in Event Data 3. Event Name itself
        event_name = event_data.get('name')
        event_tokens = game.get('event_tokens', {})
        final_token = event_tokens.get(event_name) or event_data.get('token') or event_name

        params = {
            "app_token": game.get("app_token"),
            "event_token": final_token,
            "created_at": created_at,
            "environment": game.get("environment", "production")
        }
        if device_os == "ios":
            if user.get("idfa"):
                params["idfa"] = user.get("idfa")
            if user.get("idfv"):
                params["idfv"] = user.get("idfv")
        else:
            params["gps_adid"] = gps_adid
        
        if user.get("adjust_id"): params["adid"] = user.get("adjust_id")
        if device_os != "ios" and user.get("android_id"):
            params["android_id"] = user.get("android_id")
        if user.get("ip"): params["ip_address"] = user.get("ip")
        
        headers_to_send = {
            "User-Agent": user.get("ua") or _default_ua(device_os),
            "Client-SDK": "ios4.29.0" if device_os == "ios" else "android4.28.0"
        }
        headers_for_log = dict(headers_to_send)
        if user.get("proxy_display"):
            headers_for_log["_nexus_proxy"] = user.get("proxy_display")
        
        try:
            await asyncio.sleep(random.uniform(0.5, 2.0))
            start_time = time.time()
            resp = await client.get(url, params=params, headers=headers_to_send)
            response_time_ms = (time.time() - start_time) * 1000
            
            color = "green" if resp.status_code < 400 else "red"
            await self.send_log(f"[{color}]📡 Adjust Resp ({resp.status_code}):[/] {resp.text[:100]}")
            
            # For GET request, body is empty or params string
            req_body = json.dumps(params)
            return resp.status_code, resp.text, headers_for_log, req_body, dict(resp.headers), response_time_ms
        except Exception as e:
            return 500, str(e), headers_for_log, json.dumps(params), {}, 0.0

    async def _fire_singular(self, client, game, event_data, user):
        url = "https://sdk-api.singular.net/api/v1/event"
        
        # Template Logic for Singular
        raw_event_name = event_data.get('name')
        raw_event_value = event_data.get('value', "{}")
        
        templates = game.get('event_templates', {})
        # Check for specific event template or generic level_up
        template = templates.get(raw_event_name) or (templates.get('level_up') if 'level' in raw_event_name.lower() else None)

        final_event_name = raw_event_name
        final_event_value = raw_event_value

        if template:
            # Simple replacement support for {LEVEL}
            lvl = event_data.get('level', '1')
            
            if isinstance(template, dict):
                tmpl_name = template.get('event_name', raw_event_name)
                tmpl_val = template.get('json_template', raw_event_value)
                
                final_event_name = tmpl_name.replace("{LEVEL}", str(lvl))
                final_event_value = tmpl_val.replace("{LEVEL}", str(lvl))
            elif isinstance(template, str):
                 # If template is just a string, it maps the name
                 final_event_name = template.replace("{LEVEL}", str(lvl))

        device_os = (user.get("device_os") or "android").lower()
        muid = None
        if device_os == "ios":
            muid = user.get("idfa") or None
        else:
            muid = user.get("gaid") or None

        params = {
            "a": game.get("api_key"),
            "i": game.get("app_id"),
            "p": "ios" if device_os == "ios" else "android",
            "muid": muid or str(uuid.uuid4()).upper(),
            "n": final_event_name,
            "v": final_event_value,
            "timestamp": str(int(time.time()))
        }
        
        if device_os != "ios" and user.get("android_id"):
            params["andi"] = user.get("android_id")
        if user.get("ip"): params["ip"] = user.get("ip")
        params["ve"] = user.get("sdk_ver") or "S3.1.2"
        
        try:
            start_time = time.time()
            resp = await client.get(url, params=params)
            response_time_ms = (time.time() - start_time) * 1000
            
            color = "green" if resp.status_code < 400 else "red"
            await self.send_log(f"[{color}]📡 Singular Resp ({resp.status_code}):[/] {resp.text[:100]}")
            
            req_headers = {}
            if user.get("proxy_display"):
                req_headers["_nexus_proxy"] = user.get("proxy_display")
            return resp.status_code, resp.text, req_headers, json.dumps(params), dict(resp.headers), response_time_ms
        except Exception as e:
            req_headers = {}
            if user.get("proxy_display"):
                req_headers["_nexus_proxy"] = user.get("proxy_display")
            return 500, str(e), req_headers, json.dumps(params), {}, 0.0

kun_engine = KUNNexusEngine()

def set_engine_log_queue(q):
    """دالة لحقن طابور السجلات من الملف الرئيسي إلى المحرك"""
    global log_queue
    log_queue = q
