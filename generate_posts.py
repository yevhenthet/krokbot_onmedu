#!/usr/bin/env python3
"""
Generate Telegram posts for KROK-1 microbiology MCQs.
Usage: ANTHROPIC_API_KEY=sk-... python3 generate_posts.py [--start 1] [--end 464] [--batch 20]
"""

import json, argparse, time, os, sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Run: pip install anthropic")
    sys.exit(1)

SYSTEM_PROMPT = """Ти — викладач мікробіології, який створює освітні пости для Telegram-каналу з підготовки до КРОК-1.

Для кожного тестового питання КРОК-1 з мікробіології сформуй пост у точному форматі:

🧪 ТЕСТ ДНЯ
[текст питання]

A. [варіант A]
B. [варіант B]
C. [варіант C]
D. [варіант D]
E. [варіант E]

✅ Відповідь: А. [правильна відповідь] ([коротка назва теми/діагнозу])
  КЛЮЧОВІ СЛОВА — [назва теми]
     [ключовий клінічний/мікробіологічний факт 1]
     [ключовий факт 2]
     [ключовий факт 3]
     [ключовий факт 4]
     [ключовий факт 5]

  Запам'ятайте: "[коротке мнемонічне правило або формула]"

Правила:
- Ключових фактів — 4-6, лаконічно, кожен з маленьким emoji (🔬, 🧫, 💉, ⚠️, 🦠, 🔴, 🩸 тощо)
- Мнемоніка — одне речення у лапках, легко запам'ятовується
- Назва теми в "Відповідь" — діагноз або мікроорганізм, стисло
- Тільки текст, без markdown, без зайвих пояснень
- Якщо варіант E = "-", не показуй його"""

USER_TEMPLATE = """Питання id={id} (тема: {topic}):
{question}

Варіанти:
A. {A}
B. {B}
C. {C}
D. {D}
E. {E}

Правильна відповідь: A. {A}"""


def format_question(q):
    opts = q['options']
    topic = q.get('topic') or 'Мікробіологія'
    return USER_TEMPLATE.format(
        id=q['id'],
        topic=topic,
        question=q['question'],
        A=opts.get('A', '-'),
        B=opts.get('B', '-'),
        C=opts.get('C', '-'),
        D=opts.get('D', '-'),
        E=opts.get('E', '-'),
    )


def generate_post(client, q, retries=3):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": format_question(q)}]
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return f"[ПОМИЛКА ГЕНЕРАЦІЇ: {e}]"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--end', type=int, default=None)
    parser.add_argument('--batch', type=int, default=20, help='Save every N questions')
    parser.add_argument('--delay', type=float, default=0.3, help='Delay between API calls (sec)')
    args = parser.parse_args()

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    with open('krok1_microbiology_mcqs.json', encoding='utf-8') as f:
        qs = json.load(f)['questions']

    # Filter by range
    qs_to_process = [q for q in qs if q['id'] >= args.start]
    if args.end:
        qs_to_process = [q for q in qs_to_process if q['id'] <= args.end]

    output_file = Path('telegram_posts.json')
    # Load existing results
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
    print(f"Генерую {total} постів (id {args.start}–{args.end or 'кінець'})...")

    for i, q in enumerate(qs_to_process, 1):
        post = generate_post(client, q)
        results.append({'id': q['id'], 'sheet': q['sheet'], 'topic': q.get('topic'), 'post': post})
        print(f"[{i}/{total}] id={q['id']} ✓")

        if i % args.batch == 0:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  → збережено {len(results)} постів")

        time.sleep(args.delay)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Also export as plain text
    txt_file = Path('telegram_posts.txt')
    with open(txt_file, 'w', encoding='utf-8') as f:
        for r in sorted(results, key=lambda x: x['id']):
            f.write(r['post'])
            f.write('\n\n' + '─' * 50 + '\n\n')

    print(f"\nГотово! {len(results)} постів збережено:")
    print(f"  JSON: {output_file}")
    print(f"  TXT:  {txt_file}")


if __name__ == '__main__':
    main()
