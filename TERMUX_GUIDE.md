# دليل تشغيل مشروع KUN S2S Nexus على Termux

## المتطلبات
- Termux حديث
- Python 3

## التثبيت
```bash
pkg update -y
pkg install -y python git clang rust openssl libffi
pip install --upgrade pip
pip install -r requirements.txt
```

## الإعداد
انسخ ملف الإعدادات ثم عدّل التوكنات:
```bash
cp .env.example .env
nano .env
```

الحد الأدنى:
```env
BOT_TOKENS=TOKEN_1
ADMIN_ID=123456789
```

## التشغيل
```bash
python main.py
```

## التشغيل بدون واجهة TUI (موصى به على الأجهزة الضعيفة)
```bash
KUN_HEADLESS=1 python main.py
```

## تغيير منفذ لوحة الويب
```bash
DASH_PORT=8000 DASH_HOST=0.0.0.0 python main.py
```

