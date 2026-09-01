import sqlite3

import config

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
                notifications_enabled INTEGER NOT NULL DEFAULT 1,
                bonus_balance INTEGER NOT NULL DEFAULT 0,  -- бонусы «день отдыха», заполняются вручную
                out_of_game INTEGER NOT NULL DEFAULT 0     -- 1 = выбыл из розыгрыша
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
                    result_reason IN ('ok', 'late', 'lt_10k', 'no_submission', 'bonus')
                ),
                bonus_used INTEGER NOT NULL DEFAULT 0,     -- 0/1: был ли списан бонус за этот день
                UNIQUE(user_id, day_msk),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS seasons (
                name TEXT PRIMARY KEY,
                type TEXT NOT NULL CHECK (type IN ('passive', 'active')),
                date_from TEXT NOT NULL,
                date_to TEXT NOT NULL,
                daily_goal INTEGER NOT NULL DEFAULT 0,
                passive_avg_threshold INTEGER NOT NULL DEFAULT 0,
                entry_fee INTEGER NOT NULL DEFAULT 0
            );
        """)

        # История начислений и списаний бонусов — для /bonuses и для отката.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bonus_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                day_msk TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Журнал действий организатора (оплаты, выбытия, бонусы) — п.4 ТЗ.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_tg_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_user_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS draw_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                bank INTEGER NOT NULL,
                organizer_share INTEGER NOT NULL,
                winner_share INTEGER NOT NULL,
                winners TEXT NOT NULL
            );
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_day ON daily_status(day_msk);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_user_day ON daily_status(user_id, day_msk);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_status_result ON daily_status(result);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bonus_log_user ON bonus_log(user_id);")

        _migrate(cur)
        _seed_seasons(cur)
        conn.commit()


def _column_names(cur, table: str):
    cur.execute("PRAGMA table_info({})".format(table))
    return {row[1] for row in cur.fetchall()}


def _migrate(cur):
    """Идемпотентно доводит боевую базу до актуальной схемы."""
    user_cols = _column_names(cur, "users")
    for column, ddl in (
        ("paid_september", "INTEGER NOT NULL DEFAULT 0"),
        ("paid_october", "INTEGER NOT NULL DEFAULT 0"),
        ("paid_november", "INTEGER NOT NULL DEFAULT 0"),
        # причина выбытия: NULL | 'violation' | 'unpaid' | 'manual'
        ("out_reason", "TEXT"),
        ("reminder_time", "TEXT NOT NULL DEFAULT '22:00'"),
    ):
        if column not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN {} {}".format(column, ddl))

    day_cols = _column_names(cur, "daily_status")
    for column, ddl in (
        ("violation", "INTEGER NOT NULL DEFAULT 0"),
        ("season", "TEXT"),
    ):
        if column not in day_cols:
            cur.execute("ALTER TABLE daily_status ADD COLUMN {} {}".format(column, ddl))


def _seed_seasons(cur):
    """Заливает сезоны из config.SEASONS (перезаписывает параметры при изменении конфига)."""
    for s in config.SEASONS:
        cur.execute("""
            INSERT INTO seasons (name, type, date_from, date_to, daily_goal, passive_avg_threshold, entry_fee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type = excluded.type,
                date_from = excluded.date_from,
                date_to = excluded.date_to,
                daily_goal = excluded.daily_goal,
                passive_avg_threshold = excluded.passive_avg_threshold,
                entry_fee = excluded.entry_fee
        """, (
            s["name"], s["type"], s["date_from"], s["date_to"],
            s["daily_goal"], s["passive_avg_threshold"], s["entry_fee"],
        ))


def _now_iso() -> str:
    return config.now_msk().isoformat(timespec="seconds")


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


# ---------- бонусы «день отдыха» ----------

def get_bonus_balance(user_id: int) -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bonus_balance FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_bonus_balance(user_id: int, value: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET bonus_balance = ? WHERE id = ?",
            (max(0, int(value)), user_id)
        )
        conn.commit()


def adjust_bonus_balance(user_id: int, delta: int, reason: str = None, day_msk: str = None) -> int:
    """Меняет баланс на delta (не опускаясь ниже 0). Возвращает новый баланс.

    Если передан reason — движение попадает в bonus_log (история для /bonuses).
    В лог пишется фактическая дельта: попытка списать больше, чем есть, урежется.
    """
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bonus_balance FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if row is None:
            return 0
        before = int(row[0])
        after = max(0, before + int(delta))
        cur.execute("UPDATE users SET bonus_balance = ? WHERE id = ?", (after, user_id))
        if reason and after != before:
            cur.execute("""
                INSERT INTO bonus_log (user_id, delta, reason, day_msk, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, after - before, reason, day_msk, _now_iso()))
        conn.commit()
        return after


def get_bonus_log(user_id: int, limit: int = 50):
    """История начислений и списаний: [(delta, reason, day_msk, created_at), ...]."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT delta, reason, day_msk, created_at
            FROM bonus_log
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit))
        return cur.fetchall()


def has_bonus_reason(user_id: int, reason: str) -> bool:
    """Был ли уже начислен бонус с такой причиной (защита от двойного начисления)."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM bonus_log WHERE user_id = ? AND reason = ? LIMIT 1",
            (user_id, reason)
        )
        return cur.fetchone() is not None


def get_user_bonus_days(user_id: int):
    """Дни, за которые был списан бонус «день отдыха»."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT day_msk, steps_value
            FROM daily_status
            WHERE user_id = ? AND bonus_used = 1
            ORDER BY day_msk
        """, (user_id,))
        return cur.fetchall()


# ---------- выбытие из розыгрыша ----------

def get_out_of_game(user_id: int) -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT out_of_game FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_out_of_game(user_id: int, value: int, reason: str = None):
    """reason: 'violation' | 'unpaid' | 'manual'. При возврате в игру причина сбрасывается."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET out_of_game = ?, out_reason = ? WHERE id = ?",
            (1 if value else 0, reason if value else None, user_id)
        )
        conn.commit()


def get_out_state(user_id: int):
    """(out_of_game, out_reason)."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT out_of_game, out_reason FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return (int(row[0]), row[1]) if row else (0, None)


def recompute_out_of_game(user_id: int) -> int:
    """
    Пересчитывает выбытие по дневным результатам активных месяцев.

    Ручное выбытие ('manual') и выбытие за неоплату ('unpaid') не трогаем —
    их снимает только организатор через /unset_out. Пересчитываем только
    выбытие за нарушение, чтобы правка шагов задним числом могла вернуть в игру.
    Возвращает новый статус.
    """
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT out_of_game, out_reason FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if row is None:
            return 0
        out_now, reason = int(row[0]), row[1]
        if out_now and reason in ("manual", "unpaid"):
            return 1

        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status d
            JOIN seasons s
              ON s.type = 'active'
             AND d.day_msk BETWEEN s.date_from AND s.date_to
            WHERE d.user_id = ?
              AND d.result = '-'
        """, (user_id,))
        has_violation = int(cur.fetchone()[0] or 0) > 0

        out = 1 if has_violation else 0
        cur.execute(
            "UPDATE users SET out_of_game = ?, out_reason = ? WHERE id = ?",
            (out, "violation" if out else None, user_id)
        )
        conn.commit()
        return out


def get_users_missing_status_for_day(day_msk: str, season_name: str = None):
    """
    Участники, допущенные к дню, у которых нет записи за этот день.

    Для активного месяца допуск = оплачен этот месяц и нет выбытия.
    Возвращает [(user_id, tg_id, bonus_balance), ...].
    """
    paid_filter = ""
    if season_name in config.PAID_COLUMN:
        # имя колонки берётся из белого списка config.PAID_COLUMN, подстановка безопасна
        paid_filter = " AND u.{} = 1".format(config.PAID_COLUMN[season_name])

    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.tg_id, u.bonus_balance
            FROM users u
            WHERE u.is_active = 1
              AND u.out_of_game = 0
              {}
              AND NOT EXISTS (
                  SELECT 1 FROM daily_status d
                  WHERE d.user_id = u.id AND d.day_msk = ?
              )
        """.format(paid_filter), (day_msk,))
        return cur.fetchall()


def count_in_game() -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND out_of_game = 0")
        return int(cur.fetchone()[0] or 0)


def count_out_of_game() -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND out_of_game = 1")
        return int(cur.fetchone()[0] or 0)


def get_in_game_users():
    """Участники, ещё не выбывшие из розыгрыша: [(id, tg_id, username, first_name), ...]."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, username, first_name
            FROM users
            WHERE is_active = 1 AND out_of_game = 0
            ORDER BY id ASC
        """)
        return cur.fetchall()


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
            SELECT id, user_id, day_msk, steps_value, submitted_on_time, result, result_reason, bonus_used
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
    result_reason: str,
    bonus_used: int = 0,
    violation: int = 0,
    season: str = None
):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_status (
                user_id, day_msk, steps_value, submitted_on_time,
                result, result_reason, bonus_used, violation, season
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, day_msk)
            DO UPDATE SET
                steps_value = excluded.steps_value,
                submitted_on_time = excluded.submitted_on_time,
                result = excluded.result,
                result_reason = excluded.result_reason,
                bonus_used = excluded.bonus_used,
                violation = excluded.violation,
                season = excluded.season
        """, (
            user_id, day_msk, steps_value, 1 if submitted_on_time else 0,
            result, result_reason, 1 if bonus_used else 0, 1 if violation else 0, season
        ))
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
    
def get_top10_for_day(day_msk: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                u.first_name,
                u.username,
                d.steps_value
            FROM daily_status d
            JOIN users u ON u.id = d.user_id
            WHERE d.day_msk = ?
            ORDER BY d.steps_value DESC, u.id ASC
            LIMIT 10
        """, (day_msk,))
        return cur.fetchall()


def get_all_time_ranking():
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                u.id,
                u.first_name,
                u.username,
                COALESCE(SUM(d.steps_value), 0) AS total_steps
            FROM users u
            LEFT JOIN daily_status d ON d.user_id = u.id
            WHERE u.is_active = 1
            GROUP BY u.id, u.first_name, u.username
            ORDER BY total_steps DESC, u.id ASC
            LIMIT 20
        """)
        return cur.fetchall()
    
def get_user_rank_and_total_users(user_id: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            WITH totals AS (
                SELECT
                    u.id AS user_id,
                    COALESCE(SUM(d.steps_value), 0) AS total_steps
                FROM users u
                LEFT JOIN daily_status d ON d.user_id = u.id
                WHERE u.is_active = 1
                GROUP BY u.id
            )
            SELECT
                (SELECT COUNT(*) + 1 FROM totals t2 WHERE t2.total_steps > t1.total_steps) AS user_rank,
                (SELECT COUNT(*) FROM totals) AS total_users
            FROM totals t1
            WHERE t1.user_id = ?
        """, (user_id,))
        row = cur.fetchone()
        return row if row else (None, 0)
    
def get_bank_stats(date_from: str, date_to: str, user_id: int):
    with get_con() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status
            WHERE result = '-'
              AND day_msk BETWEEN ? AND ?
        """, (date_from, date_to))
        total_minus = int(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status
            WHERE result = '-'
              AND user_id = ?
              AND day_msk BETWEEN ? AND ?
        """, (user_id, date_from, date_to))
        user_minus = int(cur.fetchone()[0] or 0)

        return total_minus, user_minus


def get_user_rank_for_day(day_msk: str, user_id: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            WITH day_totals AS (
                SELECT
                    u.id AS user_id,
                    COALESCE(d.steps_value, 0) AS steps_value
                FROM users u
                LEFT JOIN daily_status d
                    ON d.user_id = u.id
                   AND d.day_msk = ?
                WHERE u.is_active = 1
            )
            SELECT
                (
                    SELECT COUNT(*) + 1
                    FROM day_totals t2
                    WHERE t2.steps_value > t1.steps_value
                       OR (t2.steps_value = t1.steps_value AND t2.user_id < t1.user_id)
                ) AS user_rank,
                (SELECT COUNT(*) FROM day_totals) AS total_users
            FROM day_totals t1
            WHERE t1.user_id = ?
        """, (day_msk, user_id))
        row = cur.fetchone()
        return row if row else (None, 0)
    

def get_top3_total(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.username, u.first_name, SUM(d.steps_value) AS total_steps
            FROM daily_status d
            JOIN users u ON u.id = d.user_id
            WHERE d.day_msk BETWEEN ? AND ?
            GROUP BY u.id
            ORDER BY total_steps DESC
            LIMIT 3
        """, (date_from, date_to))
        return cur.fetchall()


def get_max_day(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.username, u.first_name, d.steps_value, d.day_msk
            FROM daily_status d
            JOIN users u ON u.id = d.user_id
            WHERE d.day_msk BETWEEN ? AND ?
            ORDER BY d.steps_value DESC
            LIMIT 1
        """, (date_from, date_to))
        return cur.fetchone()


def get_min_day_nonzero(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.username, u.first_name, d.steps_value, d.day_msk
            FROM daily_status d
            JOIN users u ON u.id = d.user_id
            WHERE d.day_msk BETWEEN ? AND ?
              AND d.result_reason != 'no_submission'
              AND d.steps_value > 0
            ORDER BY d.steps_value ASC
            LIMIT 1
        """, (date_from, date_to))
        return cur.fetchone()


def get_min_total_full_days(date_from: str, date_to: str, total_days: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            WITH per_user AS (
                SELECT
                    d.user_id,
                    COUNT(*) AS days_count,
                    SUM(d.steps_value) AS total_steps,
                    SUM(CASE WHEN d.result_reason = 'no_submission' THEN 1 ELSE 0 END) AS no_submission_count
                FROM daily_status d
                WHERE d.day_msk BETWEEN ? AND ?
                GROUP BY d.user_id
            )
            SELECT u.username, u.first_name, p.total_steps
            FROM per_user p
            JOIN users u ON u.id = p.user_id
            WHERE p.days_count = ?
              AND p.no_submission_count = 0
            ORDER BY p.total_steps ASC
            LIMIT 1
        """, (date_from, date_to, total_days))
        return cur.fetchone()


def get_summary_totals(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(steps_value), 0)
            FROM daily_status
            WHERE day_msk BETWEEN ? AND ?
        """, (date_from, date_to))
        total_steps = int(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT COUNT(*)
            FROM daily_status
            WHERE day_msk BETWEEN ? AND ?
              AND result = '-'
        """, (date_from, date_to))
        minus_count = int(cur.fetchone()[0] or 0)

        return total_steps, minus_count

def get_users_without_penalties(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*)
            FROM users u
            WHERE u.is_active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM daily_status d
                  WHERE d.user_id = u.id
                    AND d.day_msk BETWEEN ? AND ?
                    AND d.result = '-'
              )
        """, (date_from, date_to))
        return int(cur.fetchone()[0] or 0)


def get_top50_all_time_stats(date_from: str, date_to: str, total_days: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                u.username,
                u.first_name,
                COALESCE(SUM(d.steps_value), 0) AS total_steps,
                COALESCE(SUM(CASE WHEN d.result = '-' THEN 1 ELSE 0 END), 0) AS minus_count
            FROM users u
            LEFT JOIN daily_status d
                ON d.user_id = u.id
               AND d.day_msk BETWEEN ? AND ?
            WHERE u.is_active = 1
            GROUP BY u.id, u.username, u.first_name
            ORDER BY total_steps DESC, u.id ASC
            LIMIT 50
        """, (date_from, date_to))
        rows = cur.fetchall()

        # посчитаем средние на питоне
        return [
            (username, first_name, total_steps, int(total_steps / total_days) if total_days > 0 else 0, minus_count)
            for (username, first_name, total_steps, minus_count) in rows
        ]


def get_top10_penalties(date_from: str, date_to: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                u.username,
                u.first_name,
                COUNT(*) AS minus_count
            FROM daily_status d
            JOIN users u ON u.id = d.user_id
            WHERE d.day_msk BETWEEN ? AND ?
              AND d.result = '-'
            GROUP BY u.id, u.username, u.first_name
            ORDER BY minus_count DESC, u.id ASC
        """, (date_from, date_to))
        return cur.fetchall()



# ================= оплаты активных месяцев =================

def _paid_column(month: str) -> str:
    column = config.PAID_COLUMN.get(month)
    if column is None:
        raise ValueError("Неизвестный активный месяц: {}".format(month))
    return column


def set_payment(user_id: int, month: str, paid: int):
    column = _paid_column(month)  # белый список, подстановка безопасна
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET {} = ? WHERE id = ?".format(column),
            (1 if paid else 0, user_id)
        )
        conn.commit()


def get_payment(user_id: int, month: str) -> int:
    column = _paid_column(month)
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT {} FROM users WHERE id = ?".format(column), (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def get_payments(user_id: int):
    """{'september': 0/1, 'october': 0/1, 'november': 0/1}."""
    columns = [config.PAID_COLUMN[m] for m in config.ACTIVE_SEASONS]
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT {} FROM users WHERE id = ?".format(", ".join(columns)),
            (user_id,)
        )
        row = cur.fetchone()
        if row is None:
            return {m: 0 for m in config.ACTIVE_SEASONS}
        return {m: int(v) for m, v in zip(config.ACTIVE_SEASONS, row)}


def count_paid(month: str) -> int:
    column = _paid_column(month)
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE is_active = 1 AND {} = 1".format(column)
        )
        return int(cur.fetchone()[0] or 0)


def get_unpaid_in_game_users(month: str):
    """Кто ещё в игре, но не оплатил месяц: [(user_id, tg_id, username, first_name), ...]."""
    column = _paid_column(month)
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, username, first_name
            FROM users
            WHERE is_active = 1
              AND out_of_game = 0
              AND {} = 0
            ORDER BY id
        """.format(column))
        return cur.fetchall()


def get_draw_candidates():
    """
    Кандидаты в розыгрыш: [(user_id, tg_id, username, first_name), ...].

    По умолчанию (REQUIRE_PAYMENT = False) — все, кто не выбыл.
    Если допуск по оплате включён — дополнительно нужны отметки за все активные месяцы.
    """
    where = ["is_active = 1", "out_of_game = 0"]
    if config.REQUIRE_PAYMENT:
        where += ["{} = 1".format(config.PAID_COLUMN[m]) for m in config.ACTIVE_SEASONS]

    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, username, first_name
            FROM users
            WHERE {}
            ORDER BY id
        """.format(" AND ".join(where)))
        return cur.fetchall()


# ================= сезоны =================

def get_season(name: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, type, date_from, date_to, daily_goal, passive_avg_threshold, entry_fee
            FROM seasons WHERE name = ?
        """, (name,))
        row = cur.fetchone()
        if row is None:
            return None
        keys = ("name", "type", "date_from", "date_to",
                "daily_goal", "passive_avg_threshold", "entry_fee")
        return dict(zip(keys, row))


def get_season_avg(user_id: int, date_from: str, date_to: str):
    """(среднее шагов за день, число записанных дней) в периоде."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(AVG(steps_value), 0), COUNT(*)
            FROM daily_status
            WHERE user_id = ? AND day_msk BETWEEN ? AND ?
        """, (user_id, date_from, date_to))
        row = cur.fetchone()
        return int(row[0] or 0), int(row[1] or 0)


def get_today_steps(user_id: int, day_msk: str) -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT steps_value FROM daily_status WHERE user_id = ? AND day_msk = ?",
            (user_id, day_msk)
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


# ================= напоминания =================

def set_reminder_time(user_id: int, hhmm: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET reminder_time = ? WHERE id = ?", (hhmm, user_id))
        conn.commit()


def get_reminder_time(user_id: int) -> str:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT reminder_time FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else "22:00"


def get_users_for_reminder(hhmm: str):
    """Кому пора слать вечернее напоминание: [(user_id, tg_id, bonus_balance), ...]."""
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, bonus_balance
            FROM users
            WHERE is_active = 1
              AND notifications_enabled = 1
              AND out_of_game = 0
              AND COALESCE(reminder_time, '22:00') = ?
        """, (hhmm,))
        return cur.fetchall()


# ================= поиск участника для админ-команд =================

def find_user(ref: str):
    """
    Находит участника по @username, telegram_id или внутреннему id.
    Возвращает (user_id, tg_id, username, first_name) или None.
    """
    ref = (ref or "").strip()
    if not ref:
        return None

    with get_con() as conn:
        cur = conn.cursor()
        if ref.startswith("@"):
            cur.execute("""
                SELECT id, tg_id, username, first_name FROM users
                WHERE lower(username) = lower(?)
            """, (ref[1:],))
            return cur.fetchone()

        if ref.isdigit():
            value = int(ref)
            cur.execute("""
                SELECT id, tg_id, username, first_name FROM users WHERE tg_id = ?
            """, (value,))
            row = cur.fetchone()
            if row:
                return row
            cur.execute("""
                SELECT id, tg_id, username, first_name FROM users WHERE id = ?
            """, (value,))
            return cur.fetchone()

        cur.execute("""
            SELECT id, tg_id, username, first_name FROM users
            WHERE lower(username) = lower(?)
        """, (ref,))
        return cur.fetchone()


def get_user_row(user_id: int):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tg_id, username, first_name, bonus_balance, out_of_game, out_reason
            FROM users WHERE id = ?
        """, (user_id,))
        return cur.fetchone()


# ================= журнал действий организатора =================

def log_admin_action(admin_tg_id: int, action: str, target_user_id: int = None, details: str = None):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admin_log (admin_tg_id, action, target_user_id, details, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (admin_tg_id, action, target_user_id, details, _now_iso()))
        conn.commit()


def get_admin_log(limit: int = 30):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT created_at, admin_tg_id, action, target_user_id, details
            FROM admin_log ORDER BY id DESC LIMIT ?
        """, (limit,))
        return cur.fetchall()


# ================= общая статистика =================

def count_total_users() -> int:
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
        return int(cur.fetchone()[0] or 0)


def get_stats_all():
    """Сводка для /stats_all: всего / в игре / выбыло / оплаты по месяцам / причины выбытия."""
    total = count_total_users()
    in_game = count_in_game()
    out = count_out_of_game()
    paid = {m: count_paid(m) for m in config.ACTIVE_SEASONS}

    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(out_reason, 'unknown'), COUNT(*)
            FROM users
            WHERE is_active = 1 AND out_of_game = 1
            GROUP BY 1
        """)
        reasons = dict(cur.fetchall())

    return {
        "total": total, "in_game": in_game, "out": out,
        "paid": paid, "out_reasons": reasons,
    }


# ================= розыгрыш =================

def save_draw_result(bank: int, organizer_share: int, winner_share: int, winners: str):
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO draw_results (created_at, bank, organizer_share, winner_share, winners)
            VALUES (?, ?, ?, ?, ?)
        """, (_now_iso(), bank, organizer_share, winner_share, winners))
        conn.commit()


def get_last_draw():
    with get_con() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT created_at, bank, organizer_share, winner_share, winners
            FROM draw_results ORDER BY id DESC LIMIT 1
        """)
        return cur.fetchone()
