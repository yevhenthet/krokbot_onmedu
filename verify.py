#!/usr/bin/env python3
"""
Verify and fix hints and posts: catches factual errors, language errors (calques,
bad grammar), and logic errors. Replaces check_posts.py and fix_errors.py.

Usage:
  source venv/bin/activate
  ANTHROPIC_API_KEY=sk-... python3 verify.py --hints              # fix all hints
  ANTHROPIC_API_KEY=sk-... python3 verify.py --posts              # fix all posts
  ANTHROPIC_API_KEY=sk-... python3 verify.py --hints --posts      # fix everything
  ANTHROPIC_API_KEY=sk-... python3 verify.py --id 6 7 8           # fix specific IDs
  ANTHROPIC_API_KEY=sk-... python3 verify.py --posts --force      # re-check already checked posts
  ANTHROPIC_API_KEY=sk-... python3 verify.py --posts --dry-run    # show fixes without applying
"""

import json, time, argparse, os, sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Run: pip install anthropic"); sys.exit(1)

MCQS_FILE  = Path('krok1_microbiology_mcqs.json')
POSTS_FILE = Path('telegram_posts.json')
LOG_FILE   = Path('verify_log.json')

MODEL_HINTS = 'claude-haiku-4-5-20251001'
MODEL_POSTS = 'claude-sonnet-4-6'

HINT_BATCH = 30
POST_BATCH = 5
HINT_MAX   = 190

# ── Prompts ───────────────────────────────────────────────────────────────────

HINT_SYSTEM = """Ви — викладач кафедри мікробіології. Перевіряєте підказки до тестів КРОК-1.

ЗАВДАННЯ: знайти і виправити помилки у підказці. Якщо помилок немає — повернути оригінал без змін.

ВИПРАВЛЯТИ ОБОВ'ЯЗКОВО:
• Невластива лексика: "відомість" → "диференціація", "розлучаючи" → "розрізняючи/диференціюючи"
• Кальки з російської: "являється" → "є", "приймає участь" → "бере участь", "приводить до" → "призводить до"
• Граматично некоректні дієприкметникові звороти
• Розмовний стиль, знаки оклику
• Логічні помилки (підказка не допомагає обрати правильну відповідь)
• Фактологічні помилки

ВИМОГИ:
• Не більше 190 символів — скорочуй при потребі
• Академічна українська, без жаргону
• НЕ називати правильну відповідь явно

Відповідай ТІЛЬКИ JSON-масивом:
[{"id": <N>, "hint": "<виправлена або оригінальна підказка>", "fixed": true/false}, ...]"""


POST_SYSTEM = """Ви — експерт-рецензент з медичної мікробіології. Перевіряєте навчальний пост на помилки.

ВИПРАВЛЯТИ:
Тип 1 — фактологічні помилки:
• Неправильне приписування захворювань збудникам
• Неправильні морфологічні/культуральні характеристики
• O-антитіла = активна інфекція; H-антитіла = реконвалесценція; Vi-антитіла = хронічне носійство
• Антитоксин ≠ анатоксин; лікування ≠ профілактика
• Плутанина середовищ, реакцій, антигенів

Тип 2 — мовні помилки:
• Кальки з рос.: "являється" → "є", "приймає участь" → "бере участь", "приводить до" → "призводить до"
• "відомість", "розлучаючи" та подібне — замінити природними українськими словами
• Граматично некоректні конструкції
• Слова іншою мовою (рос., англ.) без потреби

НЕ ВИПРАВЛЯТИ: стиль, спрощення для навчальних цілей, неповноту.

Відповідайте ЛИШЕ JSON-масивом:
[{"id": <N>, "corrections": [{"old": "точна цитата", "new": "виправлений варіант", "reason": "пояснення"}]}]
Якщо помилок немає — "corrections": []"""


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


def load_log() -> dict:
    return json.loads(LOG_FILE.read_text('utf-8')) if LOG_FILE.exists() else {}


def save_log(log: dict):
    LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), 'utf-8')


# ── Hints ─────────────────────────────────────────────────────────────────────

def _hint_prompt(batch: list[dict]) -> str:
    lines = ["Перевір та виправ підказки. Відповідай JSON-масивом.\n"]
    for q in batch:
        lines.append(f'ID={q["id"]} | Тема: {q.get("topic", "")}')
        lines.append(f'Питання: {q["question"]}')
        lines.append(f'Правильна відповідь: A. {q["options"].get("A", "")}')
        lines.append(f'Поточна підказка: {q.get("hint", "")}')
        lines.append("")
    return "\n".join(lines)


def verify_hints_batch(client, batch: list[dict]) -> dict[int, tuple[str, bool]]:
    msg = client.messages.create(
        model=MODEL_HINTS,
        max_tokens=4000,
        system=HINT_SYSTEM,
        messages=[{"role": "user", "content": _hint_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    start, end = raw.find('['), raw.rfind(']') + 1
    if start == -1:
        return {}
    parsed = json.loads(raw[start:end])
    return {item["id"]: (item["hint"], item.get("fixed", False)) for item in parsed}


def run_hints(client, target_ids: set | None, dry_run: bool):
    data = json.loads(MCQS_FILE.read_text('utf-8'))
    questions = data['questions']
    targets = [q for q in questions if q.get('hint') and (target_ids is None or q['id'] in target_ids)]

    if not targets:
        print("Немає підказок для перевірки."); return

    print(f"🔍 Підказки: {len(targets)} питань (батч={HINT_BATCH}, {MODEL_HINTS})")
    if dry_run:
        print("   [dry-run — зміни не застосовуються]")

    hints_map = {q['id']: q.get('hint', '') for q in questions}
    total_fixed = 0

    for i in range(0, len(targets), HINT_BATCH):
        batch = targets[i:i + HINT_BATCH]
        print(f"  Батч {i//HINT_BATCH + 1}: ID {batch[0]['id']}–{batch[-1]['id']} ...", end=' ', flush=True)
        try:
            result = verify_hints_batch(client, batch)
            fixed_n = sum(1 for _, (_, was_fixed) in result.items() if was_fixed)
            total_fixed += fixed_n
            if not dry_run:
                for qid, (hint, _) in result.items():
                    hints_map[qid] = _truncate(hint, HINT_MAX)
            print(f"✓ (виправлено: {fixed_n}/{len(batch)})")
        except Exception as e:
            print(f"❌ {e}")

        if not dry_run:
            for q in questions:
                if hints_map.get(q['id']):
                    q['hint'] = hints_map[q['id']]
            MCQS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')

        if i + HINT_BATCH < len(targets):
            time.sleep(0.5)

    print(f"\n✅ Підказки: перевірено {len(targets)}, виправлено {total_fixed}")


# ── Posts ─────────────────────────────────────────────────────────────────────

def _post_prompt(batch: list[dict]) -> str:
    parts = []
    for item in batch:
        q, post = item['q'], item['post']
        opts = ' | '.join(f"{k}: {v}" for k, v in q['options'].items() if v and v != '-')
        parts.append(
            f"=== ID {q['id']} | Тема: {q.get('topic','')} ===\n"
            f"Питання: {q['question']}\n"
            f"Варіанти: {opts}\n"
            f"Правильна відповідь: A. {q['options'].get('A','')}\n"
            f"Пост:\n{post}\n"
        )
    return '\n'.join(parts)


def verify_posts_batch(client, batch: list[dict]) -> list[dict]:
    msg = client.messages.create(
        model=MODEL_POSTS,
        max_tokens=4000,
        system=POST_SYSTEM,
        messages=[{"role": "user", "content": _post_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    start, end = raw.find('['), raw.rfind(']') + 1
    if start == -1:
        return []
    return json.loads(raw[start:end])


def apply_corrections(post: str, corrections: list) -> tuple[str, int]:
    applied = 0
    for c in corrections:
        old, new = c.get('old', ''), c.get('new', '')
        if old and new and old in post:
            post = post.replace(old, new)
            applied += 1
        elif old and old not in post:
            print(f"      ⚠️  Не знайдено: «{old[:60]}»")
    return post, applied


def run_posts(client, target_ids: set | None, dry_run: bool, force: bool):
    mcqs = {q['id']: q for q in json.loads(MCQS_FILE.read_text('utf-8'))['questions']}
    posts_data = json.loads(POSTS_FILE.read_text('utf-8'))
    posts = {r['id']: r for r in posts_data}
    log = load_log()

    if target_ids:
        targets = [qid for qid in sorted(target_ids) if qid in mcqs and qid in posts]
    else:
        targets = [
            r['id'] for r in posts_data
            if (force or str(r['id']) not in log.get('posts', {}))
            and r['id'] in mcqs
        ]

    if not targets:
        print("✅ Всі пости перевірені. Використай --force для повторної перевірки."); return

    print(f"🔍 Пости: {len(targets)} (батч={POST_BATCH}, {MODEL_POSTS})")
    if dry_run:
        print("   [dry-run — зміни не застосовуються]")

    if 'posts' not in log:
        log['posts'] = {}

    total_corrections = 0

    for i in range(0, len(targets), POST_BATCH):
        batch_ids = targets[i:i + POST_BATCH]
        batch = [{'q': mcqs[qid], 'post': posts[qid]['post']} for qid in batch_ids]
        print(f"  Батч {i//POST_BATCH + 1}: ID {batch_ids[0]}–{batch_ids[-1]} ...", end=' ', flush=True)

        try:
            results = verify_posts_batch(client, batch)
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(1)
            continue

        batch_fixes = 0
        has_fixes = False
        for item in results:
            qid = item['id']
            corrections = item.get('corrections', [])
            log['posts'][str(qid)] = {'corrections': len(corrections)}

            if not corrections:
                continue

            has_fixes = True
            print(f"\n    ID {qid}: {len(corrections)} виправлень")
            for c in corrections:
                print(f"      ✎ {c.get('reason','')[:70]}")

            if not dry_run:
                updated, applied = apply_corrections(posts[qid]['post'], corrections)
                posts[qid]['post'] = updated
                batch_fixes += applied
                total_corrections += applied

        if not has_fixes:
            print("✓ без помилок")
        elif not dry_run:
            print(f"  ✓ {batch_fixes} виправлень застосовано")

        if not dry_run:
            out = list(posts.values())
            POSTS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), 'utf-8')
        save_log(log)
        time.sleep(0.3)

    print(f"\n✅ Пости: перевірено {len(targets)}, виправлень {total_corrections}")
    checked = len(log.get('posts', {}))
    with_fixes = sum(1 for v in log.get('posts', {}).values() if v.get('corrections', 0) > 0)
    print(f"   Всього у лозі: {checked} перевірено, {with_fixes} мали помилки")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hints',   action='store_true', help='Verify/fix hints')
    parser.add_argument('--posts',   action='store_true', help='Verify/fix posts')
    parser.add_argument('--id',      type=int, nargs='+', help='Specific question IDs')
    parser.add_argument('--force',   action='store_true', help='Re-check already checked posts')
    parser.add_argument('--dry-run', action='store_true', help='Show fixes without applying')
    args = parser.parse_args()

    if not args.hints and not args.posts:
        parser.print_help(); sys.exit(1)

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    target_ids = set(args.id) if args.id else None

    if args.hints:
        run_hints(client, target_ids, args.dry_run)
    if args.posts:
        run_posts(client, target_ids, args.dry_run, args.force)


if __name__ == '__main__':
    main()
