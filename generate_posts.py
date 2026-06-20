#!/usr/bin/env python3
"""
Generate Telegram posts for KROK-1 MCQs.

Pipeline:
  1. Generate post (claude-sonnet-4-6, 1000 tokens)
  2. Verify generated post for factual errors (same model)
  3. If errors found — apply corrections and save

Usage:
  ANTHROPIC_API_KEY=sk-... python3 generate_posts.py
  ANTHROPIC_API_KEY=sk-... python3 generate_posts.py --start 1 --end 50
  ANTHROPIC_API_KEY=sk-... python3 generate_posts.py --no-verify   # skip step 2
"""

import json, argparse, time, os, sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Run: pip install anthropic")
    sys.exit(1)

MODEL       = "claude-sonnet-4-6"
MAX_TOKENS  = 1000   # enough for 5-6 facts + mnemonic in Ukrainian

# ── System prompts ────────────────────────────────────────────────────────────

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
🧪 ТЕСТ ДНЯ
[текст питання — без змін, дослівно]

A. [варіант A]
B. [варіант B]
C. [варіант C]
D. [варіант D]
E. [варіант E]

✅ Відповідь: А. [правильна відповідь] ([коротка назва теми/діагнозу])

КЛЮЧОВІ СЛОВА — [назва теми]
[emoji] [факт 1 — точний, конкретний]
[emoji] [факт 2]
[emoji] [факт 3]
[emoji] [факт 4]
[emoji] [факт 5]

Запам'ятайте: "[мнемонічне правило — одне речення]"

ПРАВИЛА ФОРМАТУВАННЯ:
- Ключових фактів — 4-6, кожен з emoji (🔬 🧫 💉 ⚠️ 🦠 🔴 🩸 🧬 🔵 💊)
- Якщо варіант E = "-" — не показуйте його
- Тільки текст, без markdown"""

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

# ── Helpers ───────────────────────────────────────────────────────────────────

def build_user_prompt(q: dict) -> str:
    opts = q['options']
    lines = [
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
    ]
    return "\n".join(lines)


def generate_post(client: anthropic.Anthropic, q: dict, context: list[dict]) -> str:
    """Generate post. context = last N posts for consistency."""
    messages = []
    for prev in context[-3:]:  # last 3 posts as few-shot context
        messages.append({"role": "user",    "content": build_user_prompt(prev['q'])})
        messages.append({"role": "assistant","content": prev['post']})
    messages.append({"role": "user", "content": build_user_prompt(q)})

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=GENERATE_SYSTEM,
                messages=messages,
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return f"[ПОМИЛКА ГЕНЕРАЦІЇ: {e}]"


def verify_and_fix(client: anthropic.Anthropic, q: dict, post: str) -> tuple[str, int]:
    """Verify post for factual errors. Returns (corrected_post, num_fixes)."""
    prompt = (
        f"Питання: {q['question']}\n"
        f"Правильна відповідь: A. {q['options'].get('A','')}\n"
        f"Тема: {q.get('topic','')}\n\n"
        f"Пост:\n{post}"
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=800,
                system=VERIFY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            start = raw.find('{')
            end = raw.rfind('}') + 1
            if start == -1:
                return post, 0
            result = json.loads(raw[start:end])
            if result.get('ok', True):
                return post, 0
            fixes = 0
            for c in result.get('corrections', []):
                old, new = c.get('old', ''), c.get('new', '')
                if old and new and old in post:
                    post = post.replace(old, new)
                    fixes += 1
            return post, fixes
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return post, 0
    return post, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',     type=int,   default=1)
    parser.add_argument('--end',       type=int,   default=None)
    parser.add_argument('--batch',     type=int,   default=10, help='Save every N questions')
    parser.add_argument('--delay',     type=float, default=0.3)
    parser.add_argument('--no-verify', action='store_true', help='Skip verification step')
    args = parser.parse_args()

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    with open('krok1_microbiology_mcqs.json', encoding='utf-8') as f:
        qs = json.load(f)['questions']

    qs_to_process = [q for q in qs if q['id'] >= args.start]
    if args.end:
        qs_to_process = [q for q in qs_to_process if q['id'] <= args.end]

    output_file = Path('telegram_posts.json')
    if output_file.exists():
        with open(output_file, encoding='utf-8') as f:
            results = json.load(f)
        done_ids = {r['id'] for r in results}
        print(f"Продовжуємо: вже є {len(done_ids)} постів")
    else:
        results = []
        done_ids = set()

    qs_to_process = [q for q in qs_to_process if q['id'] not in done_ids]
    total = len(qs_to_process)
    if not total:
        print("Всі пости вже згенеровані."); return

    verify_label = "" if args.no_verify else " + верифікація"
    print(f"Генерую {total} постів (модель: {MODEL}{verify_label})")

    # Context window: keeps last few generated posts for consistency
    context: list[dict] = []
    total_fixes = 0

    for i, q in enumerate(qs_to_process, 1):
        post = generate_post(client, q, context)

        fixes = 0
        if not args.no_verify and not post.startswith('[ПОМИЛКА'):
            post, fixes = verify_and_fix(client, q, post)
            total_fixes += fixes

        results.append({'id': q['id'], 'sheet': q['sheet'], 'topic': q.get('topic'), 'post': post})
        context.append({'q': q, 'post': post})

        fix_label = f" [{fixes} виправл.]" if fixes else ""
        print(f"  [{i}/{total}] id={q['id']}{fix_label} ✓")

        if i % args.batch == 0:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  → збережено {len(results)} постів")

        time.sleep(args.delay)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    txt_file = Path('telegram_posts.txt')
    with open(txt_file, 'w', encoding='utf-8') as f:
        for r in sorted(results, key=lambda x: x['id']):
            f.write(r['post'])
            f.write('\n\n' + '─' * 50 + '\n\n')

    print(f"\nГотово! {len(results)} постів. Виправлень при верифікації: {total_fixes}")
    print(f"  JSON: {output_file}")
    print(f"  TXT:  {txt_file}")


if __name__ == '__main__':
    main()
