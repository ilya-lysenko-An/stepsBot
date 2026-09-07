-- Проставляет бонусы «день отдыха» по списку из чата.
--
-- Запускать можно сколько угодно раз: у кого баланс уже совпадает, тот не
-- трогается и в историю ничего не пишется. Тех, кто ещё не нажал «УЧАСТВУЮ»,
-- в базе нет — скрипт покажет их отдельно, повторите после регистрации.
--
--   sudo -u stepsbot sqlite3 /opt/stepsbot/steps.db < /opt/stepsbot/deploy/seed_bonuses.sql

BEGIN;

CREATE TEMP TABLE seed(username TEXT PRIMARY KEY, bonuses INTEGER);
INSERT INTO seed(username, bonuses) VALUES
  ('eluenn', 3),
  ('alenkaholmes', 3),
  ('natusik_yaro', 3),
  ('Z_komarova', 3),
  ('ded_vreden', 1),
  ('lllelenka', 3),
  ('Semicvet_sn', 3),
  ('Darina_Ramdeni', 3),
  ('BogachevD', 3),
  ('Edian1987', 3),
  ('Nincha_13', 3),
  ('Sergei_S_Pivovarov', 1),
  ('Ds2dle', 2),
  ('Sokolowsky', 3),
  ('your_passive_aggressive', 0),
  ('Sanchez_K', 0),
  ('Jane_Samsonova', 0);
-- Диана Гайдук (+79508632198, 3 бонуса) — без ника в Telegram.
-- После регистрации выдать ботом: /add_bonus <её_ник_или_tg_id> 3

-- Разницу пишем в историю, чтобы /bonuses показывал движение, а не пустоту.
INSERT INTO bonus_log(user_id, delta, reason, day_msk, created_at)
SELECT u.id,
       s.bonuses - u.bonus_balance,
       CASE WHEN s.bonuses > u.bonus_balance THEN 'admin_add' ELSE 'admin_remove' END,
       NULL,
       datetime('now', '+3 hours')
FROM seed s
JOIN users u ON lower(u.username) = lower(s.username)
WHERE s.bonuses <> u.bonus_balance;

UPDATE users
SET bonus_balance = (
      SELECT s.bonuses FROM seed s WHERE lower(s.username) = lower(users.username)
    )
WHERE lower(username) IN (SELECT lower(username) FROM seed);

COMMIT;

.mode column
.headers on
SELECT s.username AS "из_списка",
       s.bonuses  AS "надо",
       CASE WHEN u.id IS NULL THEN '— НЕ НАЙДЕН, ещё не нажал УЧАСТВУЮ'
            ELSE 'ok, стало ' || u.bonus_balance END AS "результат"
FROM seed s
LEFT JOIN users u ON lower(u.username) = lower(s.username)
ORDER BY (u.id IS NULL) DESC, s.username;
