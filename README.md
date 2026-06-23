# Telegram-бот для тестів КРОК-1

Автоматична публікація тестів у Telegram-канал за розкладом:
- 🕗 **08:00** — інтерактивне питання (quiz poll із варіантами відповідей)
- 🕙 **10:00** — розбір правильної відповіді з ключовими фактами

База: JSON-файл із питаннями вашої дисципліни. Пости та підказки генеруються через Claude AI (Anthropic API).

---

## Структура файлів

```
.
├── pipeline.py          # ← єдина точка входу (оркеструє всі кроки)
│
├── parse_excel.py       # Парсинг стандартного Excel ОНМедУ (Moodle) → JSON
├── parse_claude.py      # Парсинг будь-якого формату через Claude AI
├── generate.py          # Генерація постів + підказок через Claude API
├── verify.py            # Верифікація і виправлення помилок у постах та підказках
├── upload_images.py     # Завантаження зображень у Telegram → image_ids.json
├── telegram_bot.py      # Основний бот (scheduler + відправка)
│
├── requirements.txt     # Python-залежності
├── Dockerfile           # Для деплою на fly.io
├── fly.toml             # Конфігурація fly.io
│
├── krok1_microbiology_mcqs.json   # ← база питань (не в репо)
├── telegram_posts.json            # ← згенеровані пости (не в репо)
├── image_ids.json                 # ← file_id зображень у Telegram (не в репо)
└── images/                        # ← зображення для питань (не в репо)
```

---

## Швидкий старт

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Сценарій А: нова база питань з нуля

```bash
# 1. Парсинг Excel → JSON
ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.xlsx

# 2. Генерація постів і підказок
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate

# 3. Верифікація і виправлення помилок
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify

# 4. Завантаження зображень (якщо є папка images/)
TELEGRAM_BOT_TOKEN=... python3 pipeline.py upload

# 5. Деплой на Fly.io
python3 pipeline.py deploy
```

### Сценарій Б: додати нові питання до існуючої бази

```bash
# Одна команда робить все: парсинг → мерж → генерація → верифікація
ANTHROPIC_API_KEY=sk-... python3 pipeline.py add --input new_questions.xlsx

# Потім деплой
python3 pipeline.py deploy
```

---

## Команди pipeline.py

### `parse` — парсинг нового Excel (замінює існуючу базу)

```bash
# Стандартний формат ОНМедУ (Moodle)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.xlsx

# Нестандартний формат (Claude розпізнає сам)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.xlsx --claude

# Текстовий файл (з PDF або Word)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.txt --claude
```

> ⚠️ Команда `parse` **замінює** існуючий `krok1_microbiology_mcqs.json`. Для додавання питань до існуючої бази використовуйте `add`.

---

### `add` — додати нові питання до існуючої бази

Повний автоматичний процес: парсинг → мерж → генерація → верифікація.

```bash
ANTHROPIC_API_KEY=sk-... python3 pipeline.py add --input new_questions.xlsx

# Подивитися що додасться, без змін
ANTHROPIC_API_KEY=sk-... python3 pipeline.py add --input new_questions.xlsx --dry-run

# Нестандартний формат
ANTHROPIC_API_KEY=sk-... python3 pipeline.py add --input new_questions.xlsx --claude
```

**Що відбувається всередині:**
1. Парсить новий файл у тимчасовий JSON
2. Порівнює з існуючою базою — дублікати (за текстом питання) пропускаються
3. Нові питання отримують ID що продовжують існуючу нумерацію
4. Генерує пости і підказки **тільки для нових** питань
5. Верифікує **тільки нові** питання

---

### `generate` — генерація постів і підказок

```bash
# Згенерувати все (пости + підказки)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate

# Тільки пости (без підказок)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate --posts

# Тільки підказки
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate --hints

# Генерувати пости тільки для частини питань
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate --start 1 --end 50

# Без верифікації постів (швидше, але менш точно)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate --no-verify
```

**Пайплайн генерації постів:**
1. `claude-sonnet-4-6` створює пост із ключовими фактами та мнемонікою. Останні 3 пости передаються як few-shot приклади для стилістичної послідовності.
2. Той самий модель перевіряє пост на фактологічні помилки і автоматично виправляє.
3. Результат зберігається у `telegram_posts.json` і `telegram_posts.txt`.

**Генерація підказок** (`claude-haiku-4-5`) — батчами по 50 питань. Підказка (≤190 символів) вказує на ключову диференційну ознаку, але не розкриває відповідь прямо.

**Відновлення після переривання:** обидва процеси продовжуються з місця зупинки — просто запустіть знову.

**Орієнтовна вартість:**
| Крок | Модель | Вартість |
|------|--------|----------|
| Пости (генерація + верифікація) | claude-sonnet-4-6 × 2 | ~$4–6 за 464 питання |
| Підказки | claude-haiku-4-5 | ~$0.20–0.40 за 464 питання |

---

### `verify` — верифікація і виправлення помилок

Повторний прохід по вже згенерованому контенту. Виправляє:
- **Фактологічні помилки**: неправильне приписування збудників, плутанина середовищ, O/H/Vi-антитіла, антитоксин vs анатоксин
- **Мовні помилки**: кальки з російської ("являється", "приймає участь"), граматичні конструкції, нелітературні слова

```bash
# Перевірити всі підказки і пости
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify

# Тільки підказки
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --hints

# Тільки пости
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --posts

# Конкретні питання (наприклад після ручного редагування)
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --id 6 7 8

# Повторна перевірка вже перевірених постів
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --posts --force

# Показати що б виправилось, без змін
ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --dry-run
```

Прогрес зберігається у `verify_log.json` — повторна перевірка пропускає вже перевірені пости (якщо не вказано `--force`).

**Орієнтовна вартість:** ~$1.50 за 464 постів (claude-sonnet-4-6), підказки — ~$0.05 (claude-haiku-4-5).

---

### `upload` — завантаження зображень

```bash
TELEGRAM_BOT_TOKEN=... python3 pipeline.py upload
```

Завантажує нові зображення з папки `images/` у Telegram і зберігає `file_id` у `image_ids.json`. Вже завантажені пропускаються. Зображення мають бути названі `{id}.jpg` (або `.png`, `.webp`, `.jpeg`), де `id` — номер питання.

---

### `deploy` — деплой на Fly.io

```bash
python3 pipeline.py deploy
```

Будує Docker-образ і деплоїть на Fly.io. Включає `krok1_microbiology_mcqs.json`, `telegram_posts.json`, `image_ids.json`.

---

## Формат бази питань

Файл `krok1_microbiology_mcqs.json` — результат парсингу Excel:

```json
{
  "meta": {"total": 464, "source_file": "file.xlsx"},
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
      "correct": "A",
      "hint": "Підказка ≤190 символів, не розкриває відповідь."
    }
  ]
}
```

> **Важливо:** правильна відповідь завжди варіант `A`. Бот перемішує варіанти перед відправкою — правильна ніколи не буде першою.

> Поле `hint` генерується автоматично. Якщо відсутнє — бот витягує перший факт із поста-розбору.

---

## Формат Excel ОНМедУ (Moodle)

Кожне питання займає **8 рядків** на аркуші:

```
Рядок 1: №                   (номер питання)
Рядок 2: Тема
Рядок 3: Текст питання
Рядок 4: Правильна відповідь → стає варіантом A
Рядок 5: Варіант B
Рядок 6: Варіант C
Рядок 7: Варіант D
Рядок 8: Варіант E
```

Для PDF або Word — конвертуйте у `.txt` і використовуйте `--claude`:
- PDF: `pdftotext file.pdf file.txt`
- Word: збережіть як "Звичайний текст (.txt)"

---

## Налаштування Telegram-бота

### Створення бота

1. Відкрийте [@BotFather](https://t.me/BotFather) → `/newbot`
2. Збережіть **токен** (формат: `1234567890:ABC...`)

### Отримання Chat ID каналу

1. Створіть Telegram-канал
2. Додайте бота як адміністратора з правом **"Post messages"**
3. Перешліть будь-яке повідомлення з каналу боту [@JsonDumpBot](https://t.me/JsonDumpBot)
4. У відповіді знайдіть `"forward_from_chat"` → `"id"` (від'ємне число, напр. `-1001234567890`)

---

## Деплой на Fly.io

### Встановлення та вхід

```bash
curl -L https://fly.io/install.sh | sh
flyctl auth login
```

### Перший деплой

```bash
# Відредагуйте fly.toml: замініть app = 'YOUR-APP-NAME'
flyctl launch --no-deploy --copy-config
flyctl secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=...
flyctl deploy
```

### Корисні команди

```bash
flyctl logs -a my-app          # логи бота в реальному часі
flyctl status -a my-app        # стан машини
flyctl ssh console -a my-app   # SSH у контейнер
```

---

## Що робить бот

**О 08:00** публікує в канал:
- Зображення питання (якщо є в `image_ids.json`)
- Якщо питання довше 300 символів — спочатку повний текст окремим повідомленням, потім poll з підказкою "↑ Питання вище — оберіть правильну відповідь:"
- Quiz poll із варіантами A–E (перемішані, правильна ніколи не перша)
- Кнопка підказки: натиснути → з'являється `hint` (ключова ознака без прямої відповіді)

**О 10:00** публікує розбір:
- Правильна відповідь із коротким діагнозом
- Ключові факти з emoji
- Наукові назви — курсивом

**Стан** зберігається у `/data/bot_state.json` (persistent volume на Fly.io). Бот пам'ятає останнє відправлене питання і продовжує послідовно. Після останнього питання починає з початку.

---

## Локальний запуск

```bash
source venv/bin/activate

# Dry-run — перевірити питання без відправки
TELEGRAM_BOT_TOKEN=x TELEGRAM_CHANNEL_ID=x python3 telegram_bot.py --test --id 5

# Відправити poll вручну
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --poll --id 1

# Відправити розбір вручну
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --answer --id 1

# Запустити scheduler локально
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 telegram_bot.py --schedule
```

### Зміна часу розкладу

У `telegram_bot.py`:
```python
POLL_HOUR   = 8   # година відправки питання
ANSWER_HOUR = 10  # година відправки відповіді
```

---

## Зведена вартість

| Операція | Модель | Вартість (одноразово) |
|----------|--------|----------------------|
| Парсинг через Claude | claude-haiku-4-5 | ~$0.10–0.30 / 400–500 питань |
| Генерація постів | claude-sonnet-4-6 × 2 | ~$4–6 / 464 питання |
| Генерація підказок | claude-haiku-4-5 | ~$0.20–0.40 / 464 питання |
| Верифікація постів | claude-sonnet-4-6 | ~$1.50 / 464 питання |
| Хостинг бота (Fly.io) | — | Безкоштовно (shared-cpu-1x) |
| Telegram Bot API | — | Безкоштовно |

---

## Автор

**Тарасов Євген**

Старший викладач кафедри загальної та клінічної епідеміології та біобезпеки
з курсом мікробіології та вірусології
Одеський національний медичний університет
