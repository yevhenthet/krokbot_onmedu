#!/usr/bin/env python3
"""
Generate posts and hints for KROK-1 MCQs. Replaces generate_posts.py + generate_hints.py.

Pipeline per question:
  Posts:  1. Generate (claude-sonnet-4-6)  2. Verify + fix factual errors (same model)
  Hints:  Generate in batches of 50 (claude-haiku-4-5-20251001)

Usage:
  source venv/bin/activate
  ANTHROPIC_API_KEY=sk-... python3 generate.py               # posts + hints (default)
  ANTHROPIC_API_KEY=sk-... python3 generate.py --posts       # posts only
  ANTHROPIC_API_KEY=sk-... python3 generate.py --hints       # hints only
  ANTHROPIC_API_KEY=sk-... python3 generate.py --start 1 --end 50   # posts for range
  ANTHROPIC_API_KEY=sk-... python3 generate.py --no-verify   # skip post verification
  ANTHROPIC_API_KEY=sk-... python3 generate.py --hints --force      # regenerate all hints
  ANTHROPIC_API_KEY=sk-... python3 generate.py --hints --id 1 5 10  # specific hint IDs
"""

import json, argparse, time, os, sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Run: pip install anthropic"); sys.exit(1)

MCQS_FILE  = Path('krok1_microbiology_mcqs.json')
POSTS_FILE = Path('telegram_posts.json')

MODEL_POSTS = 'claude-sonnet-4-6'
MODEL_HINTS = 'claude-haiku-4-5-20251001'

HINT_BATCH = 50
HINT_MAX   = 190

# ── Post prompts ──────────────────────────────────────────────────────────────

GENERATE_SYSTEM = """Ви — доцент кафедри мікробіології медичного університету. Створюєте точні академічні освітні пости для Telegram-каналу з підготовки до КРОК-1.

ВИМОГИ ДО ФАКТІВ (критично важливо):
• Кожен факт має бути достовірним і перевіреним
• Назви захворювань, збудників, реакцій — точні, без плутанини між схожими
• Культуральні ознаки, морфологія, патогенез — відповідно до сучасних підручників мікробіології
• Не приписуйте захворювання не тим збудникам (напр. туляремія ≠ Y. pestis)
• Не плутайте назви середовищ та їх склад
• Не плутайте лікування і профілактику (антитоксин ≠ анатоксин)
• Диморфізм Candida: утворює бластоспори та псевдоміцелій (не справжній міцелій)
• Vi-антитіла = хронічне носійство (не реконвалесценція)

ФОРМАТ ПОСТА (суворо дотримуватись):
✅ Відповідь: А. [правильна відповідь] ([коротка назва теми/діагнозу])

КЛЮЧОВІ СЛОВА — [назва теми]
[emoji] [факт 1 — точний, конкретний]
[emoji] [факт 2]
[emoji] [факт 3]
[emoji] [факт 4]
[emoji] [факт 5]

ПРАВИЛА ФОРМАТУВАННЯ:
- Ключових фактів — 4-6, кожен з emoji (🔬 🧫 💉 ⚠️ 🦠 🔴 🩸 🧬 🔵 💊)
- Тільки текст, без markdown
- НЕ додавати секцію "Запам'ятайте" або будь-які мнемонічні правила"""

VERIFY_SYSTEM = """Ви — експерт-рецензент з медичної мікробіології. Перевіряєте навчальний пост на фактологічні помилки.

Знайдіть ЛИШЕ достовірні фактологічні помилки:
1. Неправильне приписування захворювань збудникам
2. Неправильний склад культуральних середовищ
3. Неправильні морфологічні/культуральні характеристики
4. Неправильна класифікація мікроорганізмів або захворювань
5. Плутанина між лікуванням і профілактикою
6. Неправильні назви захворювань

НЕ виправляйте: стиль, спрощення для навчальних цілей, неповноту.

Відповідайте ЛИШЕ JSON:
{"ok": true} — якщо помилок немає
{"ok": false, "corrections": [{"old": "точна цитата", "new": "виправлений варіант", "reason": "пояснення"}]}"""

# ── Hint prompt ───────────────────────────────────────────────────────────────

HINT_SYSTEM = (
    "Ви — викладач кафедри мікробіології медичного університету. "
    "Ваше завдання: скласти коротку підказку до тестового завдання КРОК-1. "
    "Вимоги до підказки:\n"
    "• Висвітлити ключову диференційну ознаку або патогномонічний критерій, що дозволяє обрати правильну відповідь\n"
    "• НЕ називати правильну відповідь явно\n"
    f"• Обсяг — не більше {HINT_MAX} символів (СУВОРО: якщо перевищує — скорочуй до завершення думки)\n"
    "• Мова — українська, академічний стиль (без розмовних зворотів, без знаків оклику)\n"
    "• Починати з іменника або академічного звороту\n"
    "• Не використовувати тире '—' як риторичний прийом"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    for sep in ('.', '!', '?', ';'):
        idx = text.rfind(sep, 0, max_len)
        if idx > max_len * 0.55:
            return text[:idx + 1]
    idx = text.rfind(' ', 0, max_len - 3)
    return (text[:idx] + '...') if idx > 0 else text[:max_len]


def _build_post_prompt(q: dict) -> str:
    opts = q['options']
    return "\n".join([
        f"Питання id={q['id']} (тема: {q.get('topic', 'Мікробіологія')})",
        f"Предмет: {q.get('sheet', '')}",
        "",
        q['question'],
        "",
        f"A. {opts.get('A', '-')}",
        f"B. {opts.get('B', '-')}",
        f"C. {opts.get('C', '-')}",
        f"D. {opts.get('D', '-')}",
        f"E. {opts.get('E', '-')}",
        "",
        f"Правильна відповідь: A. {opts.get('A', '')}",
    ])


def _build_hint_prompt(batch: list[dict]) -> str:
    lines = ["Згенеруй підказки для наступних питань. Відповідай ТІЛЬКИ JSON-масивом у форматі:"]
    lines.append('[{"id": <N>, "hint": "<текст підказки>"}, ...]')
    lines.append("")
    for q in batch:
        opts_text = "; ".join(f"{k}: {v}" for k, v in q["options"].items())
        lines.append(f'ID={q["id"]} | Тема: {q.get("topic", "")}')
        lines.append(f'Питання: {q["question"]}')
        lines.append(f'Варіанти: {opts_text}')
        lines.append("")
    return "\n".join(lines)


# ── Post generation ───────────────────────────────────────────────────────────

def generate_post(client, q: dict, context: list[dict]) -> str:
    messages = []
    for prev in context[-3:]:
        messages.append({"role": "user",      "content": _build_post_prompt(prev['q'])})
        messages.append({"role": "assistant", "content": prev['post']})
    messages.append({"role": "user", "content": _build_post_prompt(q)})

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL_POSTS, max_tokens=1000,
                system=GENERATE_SYSTEM, messages=messages,
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if attempt < 2: time.sleep(2 ** attempt)
            else: return f"[ПОМИЛКА ГЕНЕРАЦІЇ: {e}]"


def verify_post(client, q: dict, post: str) -> tuple[str, int]:
    prompt = (
        f"Питання: {q['question']}\n"
        f"Правильна відповідь: A. {q['options'].get('A','')}\n"
        f"Тема: {q.get('topic','')}\n\n"
        f"Пост:\n{post}"
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL_POSTS, max_tokens=800,
                system=VERIFY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            start, end = raw.find('{'), raw.rfind('}') + 1
            if start == -1: return post, 0
            result = json.loads(raw[start:end])
            if result.get('ok', True): return post, 0
            fixes = 0
            for c in result.get('corrections', []):
                old, new = c.get('old', ''), c.get('new', '')
                if old and new and old in post:
                    post = post.replace(old, new)
                    fixes += 1
            return post, fixes
        except Exception as e:
            if attempt < 2: time.sleep(2 ** attempt)
            else: return post, 0
    return post, 0


def run_posts(client, args):
    with open(MCQS_FILE, encoding='utf-8') as f:
        qs = json.load(f)['questions']

    qs_to_process = [q for q in qs if q['id'] >= (args.start or 1)]
    if args.end:
        qs_to_process = [q for q in qs_to_process if q['id'] <= args.end]

    results = []
    done_ids = set()
    if POSTS_FILE.exists():
        with open(POSTS_FILE, encoding='utf-8') as f:
            results = json.load(f)
        done_ids = {r['id'] for r in results}
        print(f"Продовжуємо: вже є {len(done_ids)} постів")

    qs_to_process = [q for q in qs_to_process if q['id'] not in done_ids]
    if not qs_to_process:
        print("✅ Всі пости вже згенеровані."); return

    verify_label = "" if args.no_verify else " + верифікація"
    print(f"🤖 Пости: {len(qs_to_process)} питань ({MODEL_POSTS}{verify_label})")

    context = []
    total_fixes = 0
    batch_size = getattr(args, 'batch', 10)

    for i, q in enumerate(qs_to_process, 1):
        post = generate_post(client, q, context)
        fixes = 0
        if not args.no_verify and not post.startswith('[ПОМИЛКА'):
            post, fixes = verify_post(client, q, post)
            total_fixes += fixes

        results.append({'id': q['id'], 'sheet': q['sheet'], 'topic': q.get('topic'), 'post': post})
        context.append({'q': q, 'post': post})

        fix_label = f" [{fixes} виправл.]" if fixes else ""
        print(f"  [{i}/{len(qs_to_process)}] id={q['id']}{fix_label} ✓")

        if i % batch_size == 0:
            with open(POSTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  → збережено {len(results)} постів")

        time.sleep(getattr(args, 'delay', 0.3))

    with open(POSTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    txt_file = Path('telegram_posts.txt')
    with open(txt_file, 'w', encoding='utf-8') as f:
        for r in sorted(results, key=lambda x: x['id']):
            f.write(r['post'])
            f.write('\n\n' + '─' * 50 + '\n\n')

    print(f"\n✅ Пости: {len(results)} збережено, виправлень при генерації: {total_fixes}")


# ── Hint generation ───────────────────────────────────────────────────────────

def generate_hints_batch(client, batch: list[dict]) -> dict[int, str]:
    msg = client.messages.create(
        model=MODEL_HINTS, max_tokens=8000,
        system=HINT_SYSTEM,
        messages=[{"role": "user", "content": _build_hint_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    start, end = raw.find('['), raw.rfind(']') + 1
    if start == -1:
        print(f"  ⚠️  Не вдалось розпарсити: {raw[:200]}")
        return {}
    parsed = json.loads(raw[start:end])
    return {item["id"]: item["hint"] for item in parsed}


def run_hints(client, args):
    data = json.loads(MCQS_FILE.read_text(encoding='utf-8'))
    questions = data['questions']

    if hasattr(args, 'id') and args.id:
        targets = [q for q in questions if q['id'] in args.id]
    elif getattr(args, 'force', False):
        targets = questions
    else:
        targets = [q for q in questions if not q.get('hint')]

    if not targets:
        print("✅ Всі підказки вже згенеровані. Використай --force для перегенерації."); return

    print(f"🤖 Підказки: {len(targets)} питань (батч={HINT_BATCH}, {MODEL_HINTS})")

    hints_map = {q['id']: q.get('hint', '') for q in questions}
    done, errors = 0, 0

    for i in range(0, len(targets), HINT_BATCH):
        batch = targets[i:i + HINT_BATCH]
        ids = [q['id'] for q in batch]
        print(f"  Батч {i//HINT_BATCH + 1}: ID {ids[0]}–{ids[-1]} ...", end=' ', flush=True)
        try:
            result = generate_hints_batch(client, batch)
            for qid, hint in result.items():
                hints_map[qid] = _truncate(hint, HINT_MAX)
            done += len(result)
            print(f"✓ ({len(result)}/{len(batch)})")
        except Exception as e:
            print(f"❌ {e}")
            errors += 1

        for q in questions:
            if hints_map.get(q['id']):
                q['hint'] = hints_map[q['id']]
        MCQS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        if i + HINT_BATCH < len(targets):
            time.sleep(0.3)

    print(f"\n✅ Підказки: {done} збережено, {errors} батчів з помилками")
    missing = sum(1 for q in questions if not q.get('hint'))
    if missing:
        print(f"⚠️  {missing} питань без підказки — запусти знову або --force")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--posts',     action='store_true', help='Generate posts only')
    parser.add_argument('--hints',     action='store_true', help='Generate hints only')
    # Posts options
    parser.add_argument('--start',     type=int,   default=None)
    parser.add_argument('--end',       type=int,   default=None)
    parser.add_argument('--batch',     type=int,   default=10)
    parser.add_argument('--delay',     type=float, default=0.3)
    parser.add_argument('--no-verify', action='store_true')
    # Hints options
    parser.add_argument('--force',     action='store_true', help='Regenerate all hints')
    parser.add_argument('--id',        type=int,   nargs='+', help='Specific hint IDs')
    args = parser.parse_args()

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Default: generate both
    do_posts = args.posts or (not args.posts and not args.hints)
    do_hints = args.hints or (not args.posts and not args.hints)

    if do_posts:
        run_posts(client, args)
    if do_hints:
        run_hints(client, args)


if __name__ == '__main__':
    main()
