#!/usr/bin/env python3
"""
Factual review of telegram_posts.json using Claude API.

For each question + post, Claude identifies factual errors and returns
structured corrections. Corrections are applied in-place.

Usage:
  source venv/bin/activate
  python3 check_posts.py                    # check all unchecked posts
  python3 check_posts.py --force            # re-check all
  python3 check_posts.py --start 11 --end 50  # specific range
  python3 check_posts.py --dry-run          # show corrections without applying
"""

import json, time, argparse, re
from pathlib import Path
import anthropic

MCQS_FILE  = Path('krok1_microbiology_mcqs.json')
POSTS_FILE = Path('telegram_posts.json')
LOG_FILE   = Path('check_posts_log.json')
BATCH_SIZE = 5
MODEL      = 'claude-sonnet-4-6'

SYSTEM = """Ви — експерт з медичної мікробіології та імунології. Перевіряєте навчальні пости для телеграм-каналу на предмет фактологічних помилок.

Для кожного питання вам надають:
- Текст питання з варіантами відповіді
- Правильну відповідь
- Пояснювальний пост (ключові факти)

Ваше завдання: знайти ЛИШЕ ФАКТОЛОГІЧНІ ПОМИЛКИ у пояснювальному пості.

Критерії для виправлення (виправляйте ТІЛЬКИ це):
1. Неправильне приписування захворювань збудникам (напр. туляремія — не Y. pestis)
2. Неправильні культуральні/морфологічні характеристики (напр. V-форма замість сталактитного росту)
3. Неправильна клінічна інтерпретація (напр. Vi-антитіла = носійство, не реконвалесценція)
4. Неправильна класифікація мікроорганізмів або захворювань
5. Неправильні назви захворювань (напр. мікроспорія замість епідермофітія)

НЕ виправляйте:
- Стиль, тон або формулювання (якщо факт правильний)
- Неповноту (якщо написане — правильне)
- Орфографічні помилки (окрім випадків, коли змінюють зміст)
- Спрощення для навчальних цілей

Відповідайте ЛИШЕ JSON-масивом:
[
  {
    "id": <номер питання>,
    "corrections": [
      {"old": "<точна цитата з поста>", "new": "<виправлений варіант>", "reason": "<чому помилка>"}
    ]
  }
]

Якщо помилок немає — "corrections": []. Не пишіть нічого поза JSON."""


def make_prompt(batch: list[dict]) -> str:
    parts = []
    for item in batch:
        q = item['q']
        opts = ' | '.join(f"{k}: {v}" for k, v in q['options'].items())
        parts.append(
            f"=== ID {q['id']} | Тема: {q.get('topic','')} ===\n"
            f"Питання: {q['question']}\n"
            f"Варіанти: {opts}\n"
            f"Правильна відповідь: {q['correct']} ({q['options'].get(q['correct'],'?')})\n"
            f"Пост:\n{item['post']}\n"
        )
    return '\n'.join(parts)


def review_batch(client, batch):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": make_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    start = raw.find('[')
    end = raw.rfind(']') + 1
    if start == -1 or end == 0:
        print(f"    ⚠️  Не вдалось розпарсити: {raw[:100]}")
        return []
    return json.loads(raw[start:end])


def apply_corrections(post_text: str, corrections: list) -> tuple[str, int]:
    applied = 0
    for c in corrections:
        old = c.get('old', '')
        new = c.get('new', '')
        if old and new and old in post_text:
            post_text = post_text.replace(old, new)
            applied += 1
        elif old and old not in post_text:
            print(f"      ⚠️  Не знайдено: «{old[:60]}»")
    return post_text, applied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force',   action='store_true', help='Re-check already checked posts')
    parser.add_argument('--dry-run', action='store_true', help='Show corrections without applying')
    parser.add_argument('--start',   type=int, default=11, help='Start ID (default: 11)')
    parser.add_argument('--end',     type=int, default=464, help='End ID (default: 464)')
    args = parser.parse_args()

    mcqs  = {q['id']: q for q in json.loads(MCQS_FILE.read_text('utf-8'))['questions']}
    posts_data = json.loads(POSTS_FILE.read_text('utf-8'))
    posts = {r['id']: r for r in posts_data}

    log = {}
    if LOG_FILE.exists():
        log = json.loads(LOG_FILE.read_text('utf-8'))

    targets = [
        qid for qid in range(args.start, args.end + 1)
        if qid in mcqs and qid in posts
        and (args.force or str(qid) not in log)
    ]

    if not targets:
        print("✅ Всі пости перевірені. Використай --force для повторної перевірки.")
        return

    print(f"🔍 Перевіряю {len(targets)} постів (ID {targets[0]}–{targets[-1]}, батч={BATCH_SIZE})")
    if args.dry_run:
        print("   [dry-run — зміни не застосовуються]")

    client = anthropic.Anthropic()
    total_corrections = 0
    total_errors = 0

    for i in range(0, len(targets), BATCH_SIZE):
        batch_ids = targets[i:i + BATCH_SIZE]
        batch = [{'q': mcqs[qid], 'post': posts[qid]['post']} for qid in batch_ids]

        print(f"  Батч {i//BATCH_SIZE + 1}: ID {batch_ids[0]}–{batch_ids[-1]} ...", end=' ', flush=True)

        try:
            results = review_batch(client, batch)
        except Exception as e:
            print(f"❌ {e}")
            total_errors += 1
            time.sleep(1)
            continue

        batch_corrections = 0
        for item in results:
            qid = item['id']
            corrections = item.get('corrections', [])
            log[str(qid)] = {'checked': True, 'corrections': len(corrections)}

            if not corrections:
                continue

            print(f"\n    ID {qid}: {len(corrections)} виправлень")
            for c in corrections:
                print(f"      — {c.get('reason','')}")
                print(f"        OLD: {c.get('old','')[:80]}")
                print(f"        NEW: {c.get('new','')[:80]}")

            if not args.dry_run:
                updated, applied = apply_corrections(posts[qid]['post'], corrections)
                posts[qid]['post'] = updated
                batch_corrections += applied
                total_corrections += applied

        if not any(item.get('corrections') for item in results):
            print("✓ без помилок")
        elif not args.dry_run:
            print(f"  ✓ {batch_corrections} виправлень застосовано")

        # Save after every batch
        if not args.dry_run:
            out = [posts[r['id']] for r in posts_data]
            POSTS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), 'utf-8')
        LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), 'utf-8')

        time.sleep(0.3)

    print(f"\n✅ Готово: {total_corrections} виправлень, {total_errors} помилок батчів")
    checked = sum(1 for v in log.values() if v.get('checked'))
    with_fixes = sum(1 for v in log.values() if v.get('corrections', 0) > 0)
    print(f"   Перевірено: {checked} | З виправленнями: {with_fixes}")


if __name__ == '__main__':
    main()
