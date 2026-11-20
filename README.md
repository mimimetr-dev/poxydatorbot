# Poxydator AI Nutrition Bot 🍏

Персональный AI-бот наставник по дефициту калорий для Telegram.

## 🌟 Ключевые возможности

1. **Фокус на дефиците калорий**:
   * Никакого фитнес-стресса и обязательных тренировок.
   * Контрольные замеры (вес и талия) — **1 раз в месяц**.
2. **Мультимодальный ввод (Comet API)**:
   * 📸 **Фото блюда**: автоматическое распознавание состава и расчёт калорий/БЖУ.
   * 🎙 **Голосовые сообщения**: распознавание аудио и извлечение данных о блюде.
   * ✍ **Текстовые сообщения**: свободный ввод.
3. **Отказоустойчивость и модели**:
   * Основная модель: `gemini-3.8-flash`
   * Резервная модель (fallback): `gpt-6-luna`
   * Автоматический веб-поиск (Google Search) для редких или брендовых продуктов.
4. **Учёт токенов и аналитика**:
   * Логирование `prompt_tokens`, `completion_tokens` и `total_tokens` для каждого обращения в БД.
   * Команда `/tokens` для просмотра расхода.
5. **Часовой пояс**:
   * Автоматическое определение таймзоны при онбординге по текущему времени пользователя.
   * Персональное вечернее саммари в 22:00 по локальному времени.
6. **Безопасность**:
   * Whitelist на уровне middleware — доступ разрешен только указанному `ALLOWED_USER_ID`.
7. **Готовность к монетизации**:
   * Флаги включения/отключения в `.env`: Telegram Stars (`ENABLE_TELEGRAM_STARS`) и оплата картой (`ENABLE_CARD_PAYMENTS`).
8. **База данных**:
   * Архитектура на SQLAlchemy 2.0 (по умолчанию SQLite, легкая миграция на PostgreSQL).

---

## 🚀 Развертывание на сервере Ubuntu (без Docker)

### 1. Проверка портов и процессов на сервере
Чтобы убедиться, что существующее ПО не будет затронуто:
```bash
ss -tulpn
```
*Бот работает в режиме Long Polling (исходящие HTTPS запросы к Telegram и Comet API), поэтому он **не занимает входящие порты** и никак не конфликтует с существующими веб-серверами или приложениями.*

### 2. Установка зависимостей на сервере
```bash
apt update && apt install -y python3 python3-venv git
```

### 3. Клонирование репозитория
```bash
cd /opt
git clone https://github.com/mimimetr-dev/poxydatorbot.git
cd /opt/poxydatorbot
```

### 4. Создание виртуального окружения и установка библиотек
```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### 5. Настройка конфигурации (.env)
```bash
cp .env.example .env
nano .env
```
Заполните ваши боевые значения:
* `BOT_TOKEN`: токен от `@BotFather`
* `COMETAPI_KEY`: ваш ключ Comet API
* `ALLOWED_USER_ID`: `6193695760`

### 6. Запуск через systemd
```bash
cp poxydator.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable poxydator
systemctl start poxydator
systemctl status poxydator
```

### Логирование:
```bash
journalctl -u poxydator -f
```
