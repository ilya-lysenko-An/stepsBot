# Развёртывание на сервере

Проверено на Ubuntu 22.04/24.04 и Debian 12. Все команды — от пользователя с sudo.

## 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Нужен Python 3.10+. Проверить: `python3 -V`.

## 2. Отдельный пользователь и каталог

Бот не должен ходить под root — он держит токен и базу.

```bash
sudo useradd --system --home-dir /opt/stepsbot --shell /usr/sbin/nologin stepsbot
sudo mkdir -p /opt/stepsbot
sudo chown stepsbot:stepsbot /opt/stepsbot
sudo -u stepsbot git clone https://github.com/ilya-lysenko-An/stepsBot.git /opt/stepsbot
```

`git clone` в существующий пустой каталог работает штатно.

## 3. Виртуальное окружение

```bash
sudo -u stepsbot python3 -m venv /opt/stepsbot/venv
sudo -u stepsbot /opt/stepsbot/venv/bin/pip install --upgrade pip
sudo -u stepsbot /opt/stepsbot/venv/bin/pip install -r /opt/stepsbot/requirements.txt
```

## 4. Секреты

`token.env` в git не хранится — переносим с рабочей машины:

```bash
scp token.env ПОЛЬЗОВАТЕЛЬ@СЕРВЕР:/tmp/token.env
```

Затем на сервере:

```bash
sudo mv /tmp/token.env /opt/stepsbot/token.env
sudo chown stepsbot:stepsbot /opt/stepsbot/token.env
sudo chmod 600 /opt/stepsbot/token.env
```

Образец полей — в `.env.example`.

## 5. Перенос существующей базы (если есть)

Если бот уже где-то работал и там есть зарегистрированные участники:

```bash
scp steps.db ПОЛЬЗОВАТЕЛЬ@СЕРВЕР:/tmp/steps.db
```

```bash
sudo mv /tmp/steps.db /opt/stepsbot/steps.db
sudo chown stepsbot:stepsbot /opt/stepsbot/steps.db
```

Схему бот обновит сам при старте. Если базы нет — ничего не делаем, создастся пустая.

## 6. Проверка руками

```bash
sudo -u stepsbot /opt/stepsbot/venv/bin/python /opt/stepsbot/test_flow.py
```

Должно закончиться строкой `ВСЁ ЗЕЛЁНОЕ ✅`. Тест работает на временной базе и
боевую не трогает.

## 7. systemd

```bash
sudo cp /opt/stepsbot/deploy/stepsbot.service /etc/systemd/system/stepsbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now stepsbot
```

Статус и логи:

```bash
systemctl status stepsbot
journalctl -u stepsbot -f
```

Важно: бот работает через long polling. Запускать две копии одновременно нельзя —
Telegram будет отдавать обновления то одной, то другой. Если бот раньше крутился
на другой машине или в терминале, сначала остановите его там.

## 8. Обновление кода

```bash
cd /opt/stepsbot && sudo -u stepsbot git pull && sudo systemctl restart stepsbot
```

## Резервная копия базы

`steps.db` — единственное, что не восстановить из git. Ежедневный бэкап:

```bash
sudo crontab -e
```

```
0 4 * * * sqlite3 /opt/stepsbot/steps.db ".backup /opt/stepsbot/backup-$(date +\%F).db" && find /opt/stepsbot -name 'backup-*.db' -mtime +14 -delete
```

Для этого нужен `sqlite3`: `sudo apt install -y sqlite3`.
