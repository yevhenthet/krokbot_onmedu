# Telegram-бот для тестів КРОК-1

Автоматична публікація тестів у Telegram-канал за розкладом:
- 🕗 **08:00** — інтерактивне питання (quiz poll із варіантами відповідей)
- 🕙 **10:00** — розбір правильної відповіді з ключовими фактами

База: JSON-файл із питаннями вашої дисципліни. Пости генеруються через Claude AI (Anthropic API).

---

## Структура файлів

```
.
├── telegram_bot.py          # Основний бот (scheduler + відправка)
├── generate_posts.py        # Генерація постів через Claude API
├── upload_images.py         # Завантаження зображень у Telegram
├── requirements.txt         # Python-залежності
├── Dockerfile               # Для деплою на fly.io
├── fly.toml                 # Конфігурація fly.io
│
├── krok1_mcqs.json          # ← ваша база питань (не в репо)
├── telegram_posts.json      # ← згенеровані пости (не в репо)
├── image_ids.json           # ← file_id зображень (не в репо)
└── images/                  # ← зображення для питань (не в репо)
```

---

## Формат бази питань

Файл `krok1_mcqs.json` — результат парсингу Excel. Потрібна структура:

```json
{
  "questions": [
    {
      "id": 1,
      "sheet": "2017",
      "topic": "Сальмонели",
      "question": "Текст питання...",
      "options": {
        "A": "Правильна відповідь",
        "B": "Варіант Б",
        "C": "Варіант В",
        "D": "Варіант Г",
        "E": "Варіант Ґ"
      },
      "correct": "A"
    }
  ]
}
```

> **Важливо:** правильна відповідь завжди повинна бути варіантом `A`. Бот перемішує варіанти перед відправкою — правильна відповідь ніколи не буде першою у списку.

---

## Крок 1. Підготовка бази питань

### Парсинг з Excel

Якщо питання у Excel (формат ОНМедУ, 8 рядків на питання):

```
Рядок 1: №
Рядок 2: Тема
Рядок 3: Текст питання
Рядок 4: Правильна відповідь (= варіант A)
Рядок 5: Варіант B
Рядок 6: Варіант C
Рядок 7: Варіант D
Рядок 8: Варіант E
```

Напишіть скрипт парсингу або зверніться до автора репозиторію — скрипт `parse_excel.py` доступний за запитом.

---

## Крок 2. Генерація постів

### Встановлення

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install anthropic
```

### Отримання API ключа Anthropic

1. Зареєструйтесь на [console.anthropic.com](https://console.anthropic.com)
2. Перейдіть у **API Keys** → **Create Key**

### Запуск генерації

```bash
ANTHROPIC_API_KEY=sk-ant-... python3 generate_posts.py
```

Скрипт генерує `telegram_posts.json` і `telegram_posts.txt`.

Орієнтовна вартість: ~$1.5–2 за 464 питання (claude-haiku-4-5).

**Відновлення після переривання:** скрипт автоматично продовжує з місця зупинки — просто запустіть знову.

---

## Крок 3. Створення Telegram-бота

1. Відкрийте [@BotFather](https://t.me/BotFather) у Telegram
2. Виконайте `/newbot` і дотримуйтесь інструкцій
3. Збережіть **токен бота** (формат: `1234567890:ABC...`)

### Отримання Chat ID каналу

1. Створіть Telegram-канал
2. Додайте бота як адміністратора з правом **"Post messages"**
3. Перешліть будь-яке повідомлення з каналу боту [@JsonDumpBot](https://t.me/JsonDumpBot)
4. У відповіді знайдіть `"forward_from_chat"` → `"id"` (від'ємне число, напр. `-1001234567890`)

---

## Крок 4. Зображення (опціонально)

Якщо хочете додавати зображення до кожного питання:

1. Покладіть зображення у папку `images/` з назвою `{id}.jpg` (або `.png`, `.webp`)
   ```
   images/
     1.jpg   ← зображення для питання id=1
     2.png   ← зображення для питання id=2
   ```

2. Завантажте їх у Telegram (щоб не зберігати на сервері):
   ```bash
   TELEGRAM_BOT_TOKEN=... python3 upload_images.py
   ```
   Результат зберігається у `image_ids.json`.

3. При деплої на fly.io завантажте `image_ids.json` разом з іншими файлами.

---

## Крок 5. Деплой на fly.io

### Встановлення flyctl

```bash
curl -L https://fly.io/install.sh | sh
flyctl auth login
```

### Налаштування

Відредагуйте `fly.toml` — замініть `YOUR-APP-NAME` на унікальне ім'я:

```toml
app = 'my-department-krok-bot'
```

### Перший деплой

```bash
flyctl launch --no-deploy --copy-config
flyctl secrets set TELEGRAM_BOT_TOKEN=1234567890:ABC... TELEGRAM_CHANNEL_ID=-1001234567890
flyctl deploy
```

### Перевірка логів

```bash
flyctl logs
```

### Оновлення (після зміни файлів)

```bash
flyctl deploy
```

---

## Локальний запуск (без fly.io)

```bash
source venv/bin/activate

# Тест — відправити конкретне питання зараз
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --poll --id 1
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --answer --id 1

# Dry-run (без відправки)
TELEGRAM_BOT_TOKEN=x TELEGRAM_CHANNEL_ID=x python3 telegram_bot.py --test --id 5

# Запустити scheduler (8:00 poll, 10:00 answer щодня)
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --schedule
```

---

## Зміна часу розкладу

У `telegram_bot.py` відредагуйте константи:

```python
POLL_HOUR   = 8   # година відправки питання
ANSWER_HOUR = 10  # година відправки відповіді
```

---

## Що робить бот

1. **О 8:00** бот відправляє в канал:
   - Зображення (якщо є `image_ids.json`)
   - Інтерактивний quiz poll з варіантами A–E (перемішані, правильна відповідь ніколи не перша)
   - При виборі відповіді Telegram одразу показує ✅ або ❌ + короткий факт

2. **О 10:00** бот відправляє:
   - ✅ Правильна відповідь
   - КЛЮЧОВІ СЛОВА з фактами
   - Наукові назви організмів відображаються *курсивом*

3. **Стан** зберігається у `bot_state.json` — бот пам'ятає яке питання було останнім і продовжує послідовно. Після 464-го питання починає з початку.

---

## Вартість

| Сервіс | Вартість |
|--------|----------|
| Anthropic API (генерація постів, один раз) | ~$1.5–2 за 464 питання |
| fly.io (хостинг бота) | Безкоштовно (shared-cpu-1x, 256MB) |
| Telegram Bot API | Безкоштовно |

---

## Автори

Розроблено на кафедрі мікробіології ОНМедУ.
