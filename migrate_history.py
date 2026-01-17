import sqlite3
import json

DB_PATH = 'kun_nexus.db'

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing columns
    cursor.execute("PRAGMA table_info(history)")
    columns = [info[1] for info in cursor.fetchall()]
    
    new_columns = {
        'request_headers': 'TEXT DEFAULT "{}"',
        'request_body': 'TEXT DEFAULT ""',
        'response_headers': 'TEXT DEFAULT "{}"',
        'response_time_ms': 'REAL DEFAULT 0.0'
    }
    
    for col, definition in new_columns.items():
        if col not in columns:
            print(f"Adding column {col}...")
            try:
                cursor.execute(f"ALTER TABLE history ADD COLUMN {col} {definition}")
                print(f"Added {col}")
            except Exception as e:
                print(f"Error adding {col}: {e}")
        else:
            print(f"Column {col} already exists.")
            
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
