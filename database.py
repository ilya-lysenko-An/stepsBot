import sqlite3

DB_PATH = "steps.db"

def get_con():
    return sqlite3.connect(DB_PATH)

def init_con():
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    club TEXT
                    );
        """)
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    steps INTEGER NOT NULL,
                    UNIQUE(user_id, day),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                    );
        """)
        conn.commit()
        
        
