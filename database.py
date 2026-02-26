import sqlite3

DB_PATH = "steps.db"


def get_con():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    with get_con() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                club TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                notifications_enabled INTEGER NOT NULL DEFAULT 1
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day_msk TEXT NOT NULL,                     -- YYYY-MM-DD
                steps_value INTEGER NOT NULL DEFAULT 0 CHECK (steps_value >= 0 AND steps_value <= 100000),
                submitted_on_time INTEGER NOT NULL DEFAULT 0, -- 0/1
                result TEXT NOT NULL CHECK (result IN ('+', '-')),
                result_reason TEXT NOT NULL CHECK (
                    result_reason IN ('ok', 'late', 'lt_10k', 'no_submission')
                ),
                UNIQUE(user_id, day_msk),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_day ON daily_status(day_msk);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_user_day ON daily_status(user_id, day_msk);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_result ON daily_status(result);")
        conn.commit()


# ---------- users ----------

def add_user(tg_id: int, username: str = None, first_name: str = None, club: str = None):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO users (tg_id, username, first_name, club)
            VALUES (?, ?, ?, ?)
        """, (tg_id, username, first_name, club))
        conn.commit()


def get_user_id(tg_id: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        return row[0] if row else None


def update_user_club(user_id: int, club: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET club = ?
            WHERE id = ?
        """, (club, user_id))
        conn.commit()


def set_notifications_enabled(user_id: int, enabled: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET notifications_enabled = ?
            WHERE id = ?
        """, (1 if enabled else 0, user_id))
        conn.commit()


def get_user_settings(tg_id: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT notifications_enabled
            FROM users
            WHERE tg_id = ?
        """, (tg_id,))
        row = cur.fetchone()
        return row  # (notifications_enabled,) or None


def get_active_users():
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, username, first_name, club, notifications_enabled
            FROM users
            WHERE is_active = 1
        """)
        return cur.fetchall()


# ---------- daily status ----------

def get_daily_status(user_id: int, day_msk: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, day_msk, steps_value, submitted_on_time, result, result_reason
            FROM daily_status
            WHERE user_id = ? AND day_msk = ?
        """, (user_id, day_msk))
        return cur.fetchone()


def upsert_daily_status(
    user_id: int,
    day_msk: str,
    steps_value: int,
    submitted_on_time: int,
    result: str,
    result_reason: str
):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_status (
                user_id, day_msk, steps_value, submitted_on_time, result, result_reason
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, day_msk)
            DO UPDATE SET
                steps_value = excluded.steps_value,
                submitted_on_time = excluded.submitted_on_time,
                result = excluded.result,
                result_reason = excluded.result_reason
        """, (user_id, day_msk, steps_value, 1 if submitted_on_time else 0, result, result_reason))
        conn.commit()


def finalize_no_submission_for_day(day_msk: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_status (
                user_id, day_msk, steps_value, submitted_on_time, result, result_reason
            )
            SELECT
                u.id, ?, 0, 0, '-', 'no_submission'
            FROM users u
            WHERE u.is_active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM daily_status d
                  WHERE d.user_id = u.id AND d.day_msk = ?
              )
        """, (day_msk, day_msk))
        conn.commit()


def list_user_daily_results(user_id: int, date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT day_msk, steps_value, submitted_on_time, result, result_reason
            FROM daily_status
            WHERE user_id = ?
              AND day_msk BETWEEN ? AND ?
            ORDER BY day_msk
        """, (user_id, date_from, date_to))
        return cur.fetchall()


def get_user_penalty_count(user_id: int, date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status
            WHERE user_id = ?
              AND day_msk BETWEEN ? AND ?
              AND result = '-'
        """, (user_id, date_from, date_to))
        return cur.fetchone()[0]


def get_fund_stats(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status
            WHERE day_msk BETWEEN ? AND ?
              AND result = '-'
        """, (date_from, date_to))
        total_penalties = cur.fetchone()[0]

        cur.execute("""
            SELECT result_reason, COUNT(*)
            FROM daily_status
            WHERE day_msk BETWEEN ? AND ?
              AND result = '-'
            GROUP BY result_reason
        """, (date_from, date_to))
        by_reason = cur.fetchall()

        return total_penalties, by_reason

def get_user_activity_stats(user_id: int, date_from: str = None, date_to: str = None):
    with get_con() as conn:
        cur = conn.cursor()

        if date_from and date_to:
            cur.execute("""
                SELECT
                    COALESCE(SUM(steps_value), 0) AS total_steps,
                    COALESCE(SUM(CASE WHEN result = '-' THEN 1 ELSE 0 END), 0) AS minus_count,
                    COALESCE(AVG(steps_value), 0) AS avg_steps
                FROM daily_status
                WHERE user_id = ?
                  AND day_msk BETWEEN ? AND ?
            """, (user_id, date_from, date_to))
        else:
            cur.execute("""
                SELECT
                    COALESCE(SUM(steps_value), 0) AS total_steps,
                    COALESCE(SUM(CASE WHEN result = '-' THEN 1 ELSE 0 END), 0) AS minus_count,
                    COALESCE(AVG(steps_value), 0) AS avg_steps
                FROM daily_status
                WHERE user_id = ?
            """, (user_id,))

        row = cur.fetchone()
        total_steps = int(row[0] or 0)
        minus_count = int(row[1] or 0)
        avg_steps = int(row[2] or 0)
        return total_steps, minus_count, avg_steps
    
def get_leader_and_user_total(user_id: int):
    with get_con() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT user_id, SUM(steps_value) AS total_steps
            FROM daily_status
            GROUP BY user_id
            ORDER BY total_steps DESC
            LIMIT 1
        """)
        leader = cur.fetchone()  

        cur.execute("""
            SELECT COALESCE(SUM(steps_value), 0)
            FROM daily_status
            WHERE user_id = ?
        """, (user_id,))
        user_total = cur.fetchone()[0] or 0

        return leader, int(user_total)

