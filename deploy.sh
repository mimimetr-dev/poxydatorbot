#!/bin/bash
set -e

echo "=== 1. ПРОВЕРКА ЗАНЯТЫХ ПОРТОВ ==="
ss -tulpn

echo -e "\n=== 2. УСТАНОВКА СИСТЕМНЫХ ПАКЕТОВ ==="
apt update -y && apt install -y python3 python3-venv git

echo -e "\n=== 3. КЛОНИРОВАНИЕ / ОБНОВЛЕНИЕ РЕПОЗИТОРИЯ ==="
mkdir -p /opt
if [ -d "/opt/poxydatorbot/.git" ]; then
    cd /opt/poxydatorbot
    git pull origin main
else
    rm -rf /opt/poxydatorbot
    git clone https://github.com/mimimetr-dev/poxydatorbot.git /opt/poxydatorbot
    cd /opt/poxydatorbot
fi

echo -e "\n=== 4. СОЗДАНИЕ ОКРУЖЕНИЯ VENV И УСТАНОВКА БИБЛИОТЕК ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo -e "\n=== 5. НАСТРОЙКА СЛУЖБЫ SYSTEMD ==="
cp poxydator.service /etc/systemd/system/
systemctl daemon-reload

if [ ! -f ".env" ]; then
    cp .env.example .env
fi

echo -e "\n=============================================="
echo ">>> УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО! <<<"
echo "1) Откройте: nano /opt/poxydatorbot/.env"
echo "2) Вставьте BOT_TOKEN и COMETAPI_KEY"
echo "3) Запустите: systemctl enable --now poxydator"
echo "=============================================="
