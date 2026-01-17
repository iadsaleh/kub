import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.database import SessionLocal, Game


def main() -> int:
    session = SessionLocal()
    try:
        games = session.query(Game).all()
        updated = 0
        for g in games:
            g.device_os = "android"
            jd = dict(g.json_data or {})
            jd["device_os"] = "android"
            g.json_data = jd
            updated += 1
        session.commit()
        print(f"updated={updated}")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
