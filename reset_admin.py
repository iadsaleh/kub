from modules.database import SessionLocal, WebUser
import hashlib

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def reset_admin():
    db = SessionLocal()
    user = db.query(WebUser).filter(WebUser.username == "admin").first()
    if user:
        print(f"Found admin. Old hash: {user.password_hash}")
        user.password_hash = hash_password("admin123")
        db.commit()
        print("Admin password reset to 'admin123'.")
    else:
        print("Admin user not found. Creating...")
        admin = WebUser(username="admin", password_hash=hash_password("admin123"), role="admin")
        db.add(admin)
        db.commit()
        print("Admin created.")
    db.close()

if __name__ == "__main__":
    reset_admin()
