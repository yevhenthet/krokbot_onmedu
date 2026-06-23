#!/usr/bin/env python3
"""
Re-verification pass: fix language and factual errors in existing hints and posts.

Usage:
  source venv/bin/activate
  ANTHROPIC_API_KEY=sk-... python3 fix_errors.py --hints           # fix all hints
  ANTHROPIC_API_KEY=sk-... python3 fix_errors.py --posts           # fix all posts
  ANTHROPIC_API_KEY=sk-... python3 fix_errors.py --hints --posts   # fix everything
  ANTHROPIC_API_KEY=sk-... python3 fix_errors.py --id 6 7 8        # fix specific IDs only
"""

import json, time, argparse, os, sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Run: pip install anthropic"); sys.exit(1)

MCQS_FILE  = Path('krok1_microbiology_mcqs.json')
POSTS_FILE = Path('telegram_posts.json')

MODEL_HINTS = 'claude-haiku-4-5-20251001'
MODEL_POSTS = 'claude-sonnet-4-6'

HINT_BATCH = 30

# ── Prompts ───────────────────────────────────────────────────────────────────

HINT_FIX_SYSTEM = """Ви — викладач кафедри мікробіології. Перевіряєте та виправляєте підказки до тестів КРОК-1.

ЗАВДАННЯ: Знайти і виправити помилки у підказці. Якщо помилок немає — повернути підказку без змін.

ТИПОВІ ПОМИЛКИ (виправляти обов'язково):
• Невластива лексика: "відомість" → "диференціація/розрізнення", "розлучаючи" → "розрізняючи/диференціюючи"
• Дієприкметникові звороти що порушують норму: "розлучаючи X та Y" → "що розрізняє X та Y"
• Кальки з російської: "являється" → "є", "приймає участь" → "бере участь", "приводить до" → "призводить до"
• Розмовний стиль, знаки оклику
• Логічні помилки: підказка не допомагає обрати правильну відповідь
• Фактологічні помилки у підказці

ВИМОГИ ДО ВИПРАВЛЕНОЇ ПІДКАЗКИ:
• Не більше 150 символів (скорочуй при потребі)
• Академічний стиль, природна українська мова
• НЕ називати правильну відповідь явно
• Починати з іменника або академічного звороту

Відповідай ТІЛЬКИ JSON-масивом:
[{"id": <N>, "hint": "<виправлена підказка або оригінал якщо помилок немає>", "fixed": true/false}, ...]"""


POST_FIX_SYSTEM = """Ви — експерт-рецензент з медичної мікробіології. Перевіряєте навчальний пост на помилки двох типів.

ТИП 1 — ФАКТОЛОГІЧНІ помилки:
• Неправильне приписування захворювань збудникам
• Неправильні культуральні/морфологічні характеристики
• Плутанина середовищ, реакцій, антигенів
• O-антитіла = активна інфекція, H-антитіла = реконвалесценція, Vi-антитіла = хронічне носійство
• Антитоксин ≠ анатоксин (лікування vs профілактика)

ТИП 2 — МОВНІ помилки:
• Кальки з російської: "являється" → "є", "приймає участь" → "бере участь", "приводить до" → "призводить до", "вказуючи на" → "що вказує на"
• Слова невластиві науковому стилю: "відомість", "розлучаючи"
• Граматично некоректні дієприкметникові звороти

НЕ виправляйте: стиль, спрощення для навчальних цілей, неповноту.

Відповідайте ЛИШЕ JSON:
{"ok": true} — якщо помилок немає
{"ok": false, "corrections": [{"old": "точна цитата з поста", "new": "виправлений варіант", "reason": "пояснення"}]}"""


# ── Hint fixing ───────────────────────────────────────────────────────────────

def make_hint_fix_prompt(batch: list[dict]) -> str:
    lines = ["Перевір та виправ підказки. Відповідай JSON-масивом.\n"]
    for q in batch:
        opts_text = "; ".join(f"{k}: {v}" for k, v in q["options"].items() if v and v != '-')
        lines.append(f'ID={q["id"]} | Тема: {q.get("topic", "")}')
        lines.append(f'Питання: {q["question"]}')
        lines.append(f'Правильна відповідь: A. {q["options"].get("A", "")}')
        lines.append(f'Поточна підказка: {q.get("hint", "")}')
        lines.append("")
    return "\n".join(lines)


def fix_hints_batch(client, batch: list[dict]) -> dict[int, tuple[str, bool]]:
    msg = client.messages.create(
        model=MODEL_HINTS,
        max_tokens=4000,
        system=HINT_FIX_SYSTEM,
        messages=[{"role": "user", "content": make_hint_fix_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    start, end = raw.find('['), raw.rfind(']') + 1
    if start == -1:
        return {}
    parsed = json.loads(raw[start:end])
    return {item["id"]: (item["hint"], item.get("fixed", False)) for item in parsed}


def _smart_truncate(text: str, max_len: int = 150) -> str:
    if len(text) <= max_len:
        return text
    for sep in ('.', '!', '?', ';'):
        idx = text.rfind(sep, 0, max_len)
        if idx > max_len * 0.55:
            return text[:idx + 1]
    idx = text.rfind(' ', 0, max_len - 3)
    return (text[:idx] + '...') if idx > 0 else text[:max_len]


def run_fix_hints(client, target_ids: set | None = None):
    data = json.loads(MCQS_FILE.read_text(encoding='utf-8'))
    questions = data['questions']

    targets = [q for q in questions if q.get('hint') and (target_ids is None or q['id'] in target_ids)]
    if not targets:
        print("Немає підказок для перевірки."); return

    print(f"🔍 Перевіряю підказки: {len(targets)} питань (батч={HINT_BATCH}, модель={MODEL_HINTS})")
    hints_map = {q['id']: q.get('hint', '') for q in questions}
    total_fixed = 0

    for i in range(0, len(targets), HINT_BATCH):
        batch = targets[i:i + HINT_BATCH]
        ids_range = f"ID {batch[0]['id']}–{batch[-1]['id']}"
        print(f"  Батч {i//HINT_BATCH + 1}: {ids_range} ...", end=' ', flush=True)
        try:
            result = fix_hints_batch(client, batch)
            fixed_in_batch = 0
            for qid, (hint, was_fixed) in result.items():
                hints_map[qid] = _smart_truncate(hint)
                if was_fixed:
                    fixed_in_batch += 1
                    total_fixed += 1
            print(f"✓ (виправлено: {fixed_in_batch}/{len(batch)})")
        except Exception as e:
            print(f"❌ {e}")

        for q in questions:
            if hints_map.get(q['id']):
                q['hint'] = hints_map[q['id']]
        MCQS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        if i + HINT_BATCH < len(targets):
            time.sleep(0.5)

    print(f"\n✅ Підказки: перевірено {len(targets)}, виправлено {total_fixed}")


# ── Post fixing ───────────────────────────────────────────────────────────────

def fix_post(client, q: dict, post: str) -> tuple[str, int]:
    prompt = (
        f"Питання: {q['question']}\n"
        f"Правильна відповідь: A. {q['options'].get('A','')}\n"
        f"Тема: {q.get('topic','')}\n\n"
        f"Пост:\n{post}"
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL_POSTS,
                max_tokens=800,
                system=POST_FIX_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            start, end = raw.find('{'), raw.rfind('}') + 1
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
                    print(f"    ✎ {c.get('reason','')[:60]}")
            return post, fixes
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return post, 0
    return post, 0


def run_fix_posts(client, target_ids: set | None = None):
    with open(MCQS_FILE, encoding='utf-8') as f:
        qs = {q['id']: q for q in json.load(f)['questions']}
    with open(POSTS_FILE, encoding='utf-8') as f:
        posts = json.load(f)

    targets = [p for p in posts if target_ids is None or p['id'] in target_ids]
    if not targets:
        print("Немає постів для перевірки."); return

    print(f"🔍 Перевіряю пости: {len(targets)} (модель={MODEL_POSTS})")
    posts_map = {p['id']: p['post'] for p in posts}
    total_fixed = 0

    for i, rec in enumerate(targets, 1):
        qid = rec['id']
        q = qs.get(qid)
        if not q:
            continue
        fixed_post, fixes = fix_post(client, q, rec['post'])
        posts_map[qid] = fixed_post
        if fixes:
            total_fixed += fixes
            print(f"  [{i}/{len(targets)}] id={qid} — {fixes} виправл. ✓")
        else:
            print(f"  [{i}/{len(targets)}] id={qid} — ок")

        if i % 20 == 0:
            for p in posts:
                p['post'] = posts_map[p['id']]
            with open(POSTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(posts, f, ensure_ascii=False, indent=2)
            print(f"  → збережено (checkpoint)")

        time.sleep(0.3)

    for p in posts:
        p['post'] = posts_map[p['id']]
    with open(POSTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Пости: перевірено {len(targets)}, виправлень {total_fixed}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hints', action='store_true', help='Fix hints in MCQs JSON')
    parser.add_argument('--posts', action='store_true', help='Fix posts in telegram_posts.json')
    parser.add_argument('--id', type=int, nargs='+', help='Fix specific question IDs only')
    args = parser.parse_args()

    if not args.hints and not args.posts:
        parser.print_help(); sys.exit(1)

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    target_ids = set(args.id) if args.id else None

    if args.hints:
        run_fix_hints(client, target_ids)
    if args.posts:
        run_fix_posts(client, target_ids)


if __name__ == '__main__':
    main()
