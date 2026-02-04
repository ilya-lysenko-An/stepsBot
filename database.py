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

def add_users(tg_id: int, username: str, first_name: str, club: str = None):
    with get_con as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO users (tg_id, username, first_name, club)
            VALUES (?, ?, ?, ?)       
        """, (tg_id, username, first_name, club))
        conn.commit()

def get_user_id(tg_id: int):
    with get_con as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        return row[0] if row else None
    
def upsert_steps(user_id: int, day: str, steps: int):
    with get_con as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO steps (user_id, day, steps)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, day)
            DO UPDATE SET steps = excluded.steps
        """, (user_id, day, steps))
        conn.commit()

def get_top10_for_day(day: str):
    with get_con as conn:
        cur = conn.cursor()
        cur.execute(""" 
        SELECT u.first_name, u.username, s.steps
        FROM steps s
        JOIN users u ON s.user_id = u.id
        WHERE s.day = ?
        ORDER BY s.steps DESC
        LIMIT 10           
        """, (day,))
        return cur.fetchall()    
