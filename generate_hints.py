#!/usr/bin/env python3
"""
Generate Ukrainian study hints for each MCQ using Claude API.
Hints are short (≤190 chars), help understand the KEY differentiating point,
but do NOT directly state the correct answer.

Usage:
  source venv/bin/activate
  python3 generate_hints.py            # generate all missing hints
  python3 generate_hints.py --force    # regenerate all (overwrite existing)
  python3 generate_hints.py --id 1 5   # regenerate specific IDs
"""

import json, sys, time, argparse
from pathlib import Path
import anthropic

MCQS_FILE = Path('krok1_microbiology_mcqs.json')
BATCH_SIZE = 50  # questions per API call
MODEL = 'claude-haiku-4-5-20251001'  # fast + cheap for bulk generation

SYSTEM = (
    "Ви — викладач кафедри мікробіології медичного університету. "
    "Ваше завдання: скласти коротку підказку до тестового завдання КРОК-1. "
    "Вимоги до підказки:\n"
    "• Висвітлити ключову диференційну ознаку або патогномонічний критерій, що дозволяє обрати правильну відповідь\n"
    "• НЕ називати правильну відповідь явно\n"
    "• Обсяг — не більше 150 символів (СУВОРО: якщо перевищує — скорочуй до завершення думки в межах 150)\n"
    "• Мова — українська, академічний стиль (без розмовних зворотів, без знаків оклику, без слів 'шукай', 'згадай', 'пригадай')\n"
    "• Починати з іменника або дієслова у наказовому способі академічного реєстру (напр. 'Зверніть увагу...', 'Ключова ознака...', 'Диференційна діагностика...')\n"
    "• Не використовувати тире '—' як риторичний прийом; лише як пунктуаційний знак за потреби"
)


def _smart_truncate(text: str, max_len: int = 190) -> str:
    if len(text) <= max_len:
        return text
    for sep in ('.', '!', '?', ';'):
        idx = text.rfind(sep, 0, max_len)
        if idx > max_len * 0.55:
            return text[:idx + 1]
    idx = text.rfind(' ', 0, max_len - 3)
    return (text[:idx] + '...') if idx > 0 else text[:max_len]


def make_user_prompt(batch: list[dict]) -> str:
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


def generate_hints_batch(client: anthropic.Anthropic, batch: list[dict]) -> dict[int, str]:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": make_user_prompt(batch)}],
    )
    raw = msg.content[0].text.strip()
    # Extract JSON array from response
    start = raw.find('[')
    end = raw.rfind(']') + 1
    if start == -1 or end == 0:
        print(f"  ⚠️  Не вдалось розпарсити відповідь: {raw[:200]}")
        return {}
    parsed = json.loads(raw[start:end])
    return {item["id"]: item["hint"] for item in parsed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Overwrite existing hints')
    parser.add_argument('--id', type=int, nargs='+', help='Specific question IDs to (re)generate')
    args = parser.parse_args()

    data = json.loads(MCQS_FILE.read_text(encoding='utf-8'))
    questions = data['questions']

    # Determine which questions need hints
    if args.id:
        targets = [q for q in questions if q['id'] in args.id]
    elif args.force:
        targets = questions
    else:
        targets = [q for q in questions if not q.get('hint')]

    if not targets:
        print("✅ Всі питання вже мають підказки. Використай --force для перегенерації.")
        return

    print(f"🤖 Генерую підказки для {len(targets)} питань (батч={BATCH_SIZE}, модель={MODEL})")

    client = anthropic.Anthropic()
    hints_map = {q['id']: q.get('hint', '') for q in questions}

    total = len(targets)
    done = 0
    errors = 0

    for i in range(0, total, BATCH_SIZE):
        batch = targets[i:i + BATCH_SIZE]
        ids = [q['id'] for q in batch]
        print(f"  Батч {i//BATCH_SIZE + 1}: ID {ids[0]}–{ids[-1]} ...", end=' ', flush=True)
        try:
            result = generate_hints_batch(client, batch)
            for qid, hint in result.items():
                hints_map[qid] = _smart_truncate(hint, 190)
            done += len(result)
            print(f"✓ ({len(result)}/{len(batch)})")
        except Exception as e:
            print(f"❌ {e}")
            errors += 1

        # Save after every batch (progress checkpoint)
        for q in questions:
            if hints_map.get(q['id']):
                q['hint'] = hints_map[q['id']]
        MCQS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

        if i + BATCH_SIZE < total:
            time.sleep(0.3)  # mild rate-limit courtesy

    print(f"\n✅ Готово: {done} підказок збережено, {errors} батчів з помилками")
    missing = sum(1 for q in questions if not q.get('hint'))
    if missing:
        print(f"⚠️  {missing} питань без підказки — запусти знову або --force")


if __name__ == '__main__':
    main()
