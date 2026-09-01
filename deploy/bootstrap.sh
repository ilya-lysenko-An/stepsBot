#!/usr/bin/env bash
# Разворачивает бота на чистом Ubuntu/Debian. Запускать на сервере от root.
# Идемпотентно: повторный запуск обновляет код и перезапускает сервис.
set -euo pipefail

REPO="https://github.com/ilya-lysenko-An/stepsBot.git"
DIR="/opt/stepsbot"
USER_NAME="stepsbot"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Запускать от root: sudo bash $0" >&2
    exit 1
fi

say "1/6 Пакеты"
export DEBIAN_FRONTEND=noninteractive
PKGS="python3 python3-venv python3-pip git sqlite3"

have_all() {
    for b in python3 git sqlite3; do
        command -v "$b" >/dev/null 2>&1 || return 1
    done
    python3 -c 'import venv' >/dev/null 2>&1 || return 1
    return 0
}

if [ "${SKIP_APT:-0}" = "1" ]; then
    echo "пропускаю по SKIP_APT=1"
elif apt-get -o DPkg::Lock::Timeout=300 update -qq \
     && apt-get -o DPkg::Lock::Timeout=300 install -y -qq $PKGS; then
    echo "пакеты на месте"
elif have_all; then
    # apt мог не отработать из-за фонового unattended-upgrades, держащего блокировку.
    # Всё нужное уже установлено, поэтому продолжаем.
    echo "ВНИМАНИЕ: apt отработал с ошибкой, но python3, venv, git и sqlite3 в системе есть — продолжаю."
else
    echo "apt не отработал, и нужных пакетов в системе нет. Установите вручную:" >&2
    echo "  apt-get install -y $PKGS" >&2
    exit 1
fi

say "2/6 Пользователь и каталог"
id -u "$USER_NAME" >/dev/null 2>&1 || \
    useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$DIR"
chown "$USER_NAME:$USER_NAME" "$DIR"

say "3/6 Код"
if [ -d "$DIR/.git" ]; then
    sudo -u "$USER_NAME" git -C "$DIR" pull --ff-only
else
    sudo -u "$USER_NAME" git clone "$REPO" "$DIR"
fi

say "4/6 Виртуальное окружение"
[ -x "$DIR/venv/bin/python" ] || sudo -u "$USER_NAME" python3 -m venv "$DIR/venv"
sudo -u "$USER_NAME" "$DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ ! -f "$DIR/token.env" ]; then
    cat <<MSG

────────────────────────────────────────────────────────────
Нет файла $DIR/token.env — без него бот не запустится.

Скопируйте его со своего ноутбука:

    scp token.env root@$(hostname -I | awk '{print $1}'):/tmp/token.env

а затем на сервере:

    mv /tmp/token.env $DIR/token.env
    chown $USER_NAME:$USER_NAME $DIR/token.env
    chmod 600 $DIR/token.env

и запустите этот скрипт ещё раз.
────────────────────────────────────────────────────────────
MSG
    exit 1
fi
chown "$USER_NAME:$USER_NAME" "$DIR/token.env"
chmod 600 "$DIR/token.env"

say "5/6 Прогон тестов (на временной базе, боевую не трогает)"
sudo -u "$USER_NAME" "$DIR/venv/bin/python" "$DIR/test_flow.py"

say "6/6 systemd"
cp "$DIR/deploy/stepsbot.service" /etc/systemd/system/stepsbot.service
systemctl daemon-reload
systemctl enable --now stepsbot
sleep 3
systemctl restart stepsbot
sleep 3

say "Статус"
systemctl status stepsbot --no-pager -l | head -12
echo
echo "Логи в реальном времени:  journalctl -u stepsbot -f"
echo "Проверка в Telegram:      отправьте боту /myid и /stats_all"
