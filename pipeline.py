#!/usr/bin/env python3
"""
Single entry point for the KROK-1 bot content pipeline.

Commands:
  parse    — Parse Excel/text file → krok1_microbiology_mcqs.json (replaces existing)
  add      — Parse new file, merge into existing MCQs, generate + verify new questions
  generate — Generate posts + hints via Claude API
  verify   — Verify and fix errors in posts and hints
  upload   — Upload images to Telegram → image_ids.json
  deploy   — Deploy to Fly.io

Usage:
  source venv/bin/activate

  # Full pipeline (brand new question set):
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.xlsx
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify
  TELEGRAM_BOT_TOKEN=... python3 pipeline.py upload
  python3 pipeline.py deploy

  # Add new questions to existing set:
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py add --input new_questions.xlsx

  # Fix errors in specific questions:
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --id 6 7 8

  # Re-check all posts (force):
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --posts --force
"""

import argparse, subprocess, sys, os, json, tempfile
from pathlib import Path

MCQS_FILE  = Path('krok1_microbiology_mcqs.json')
POSTS_FILE = Path('telegram_posts.json')


def run(cmd: list[str], **kwargs):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_parse(args):
    if not args.input:
        print("❌ Вкажи --input <файл>"); sys.exit(1)

    ext = Path(args.input).suffix.lower()
    if ext in ('.xlsx', '.xls') and not args.claude:
        run([sys.executable, 'parse_excel.py',
             '--input', args.input,
             '--output', args.output,
             '--stats'])
    else:
        run([sys.executable, 'parse_claude.py',
             '--input', args.input,
             '--output', args.output])


def cmd_add(args):
    """Parse new file, merge into existing MCQs, then generate + verify new IDs."""
    if not args.input:
        print("❌ Вкажи --input <файл>"); sys.exit(1)
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print("❌ Встанови ANTHROPIC_API_KEY"); sys.exit(1)

    # Step 1: parse new file into a temp JSON
    tmp = Path(tempfile.mktemp(suffix='.json'))
    ext = Path(args.input).suffix.lower()
    if ext in ('.xlsx', '.xls') and not args.claude:
        run([sys.executable, 'parse_excel.py',
             '--input', args.input, '--output', str(tmp)])
    else:
        run([sys.executable, 'parse_claude.py',
             '--input', args.input, '--output', str(tmp)])

    # Step 2: merge new questions into existing MCQs
    new_ids = _merge(tmp, args.dry_run)
    tmp.unlink(missing_ok=True)

    if not new_ids:
        print("ℹ️  Нових питань не знайдено (всі вже є у базі).")
        return

    print(f"\n✅ Мерж: {len(new_ids)} нових питань — ID {new_ids[0]}–{new_ids[-1]}")
    ids_str = [str(i) for i in new_ids]

    if args.dry_run:
        print("   [dry-run — generate і verify не запускаємо]")
        return

    # Step 3: generate posts + hints for new IDs only
    print("\n── Генерація ────────────────────────────────")
    run([sys.executable, 'generate.py',
         '--posts', '--start', str(new_ids[0]), '--end', str(new_ids[-1])])
    run([sys.executable, 'generate.py',
         '--hints', '--id'] + ids_str)

    # Step 4: verify new questions
    print("\n── Верифікація ──────────────────────────────")
    run([sys.executable, 'verify.py',
         '--hints', '--posts', '--id'] + ids_str)

    print(f"\n🎉 Готово! Додано {len(new_ids)} питань (ID {new_ids[0]}–{new_ids[-1]}).")
    print("   Наступний крок: python3 pipeline.py deploy")


def _merge(new_file: Path, dry_run: bool) -> list[int]:
    """Merge questions from new_file into MCQS_FILE. Returns list of new IDs."""
    new_data  = json.loads(new_file.read_text(encoding='utf-8'))
    new_qs    = new_data.get('questions', [])

    if not MCQS_FILE.exists():
        print("⚠️  Існуючої бази немає — зберігаємо нові питання як нову базу.")
        if not dry_run:
            MCQS_FILE.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
        return [q['id'] for q in new_qs]

    existing_data = json.loads(MCQS_FILE.read_text(encoding='utf-8'))
    existing_qs   = existing_data['questions']

    # Deduplicate by first 80 chars of question text
    existing_keys = {q['question'][:80].strip().lower() for q in existing_qs}
    next_id = max(q['id'] for q in existing_qs) + 1

    added = []
    skipped = 0
    for q in new_qs:
        key = q['question'][:80].strip().lower()
        if key in existing_keys:
            skipped += 1
            continue
        q['id'] = next_id
        existing_qs.append(q)
        existing_keys.add(key)
        added.append(next_id)
        next_id += 1

    print(f"   Нових: {len(added)}, дублікатів пропущено: {skipped}")

    if added and not dry_run:
        existing_data['meta']['total'] = len(existing_qs)
        MCQS_FILE.write_text(
            json.dumps(existing_data, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    return added


def cmd_generate(args):
    cmd = [sys.executable, 'generate.py']
    if args.start:     cmd += ['--start', str(args.start)]
    if args.end:       cmd += ['--end',   str(args.end)]
    if args.no_verify: cmd += ['--no-verify']
    run(cmd)


def cmd_verify(args):
    verify_cmd = [sys.executable, 'verify.py']

    if args.hints:  verify_cmd += ['--hints']
    if args.posts:  verify_cmd += ['--posts']
    if not args.hints and not args.posts:
        verify_cmd += ['--hints', '--posts']
    if args.id:
        verify_cmd += ['--id'] + [str(i) for i in args.id]
    if args.force:   verify_cmd += ['--force']
    if args.dry_run: verify_cmd += ['--dry-run']

    run(verify_cmd)


def cmd_upload(args):
    if not os.environ.get('TELEGRAM_BOT_TOKEN'):
        print("❌ Встанови TELEGRAM_BOT_TOKEN"); sys.exit(1)
    run([sys.executable, 'upload_images.py'])


def cmd_deploy(args):
    run(['flyctl', 'deploy', '-a', 'microbiology-krok-bot', '--detach'])


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='KROK-1 bot content pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # parse
    p_parse = sub.add_parser('parse', help='Parse Excel/text → JSON (replaces existing)')
    p_parse.add_argument('--input',  required=True)
    p_parse.add_argument('--output', default='krok1_microbiology_mcqs.json')
    p_parse.add_argument('--claude', action='store_true', help='Force Claude parser')

    # add
    p_add = sub.add_parser('add', help='Add new questions from file → merge + generate + verify')
    p_add.add_argument('--input',   required=True)
    p_add.add_argument('--claude',  action='store_true', help='Force Claude parser')
    p_add.add_argument('--dry-run', action='store_true', help='Show what would be added, no changes')

    # generate
    p_gen = sub.add_parser('generate', help='Generate posts + hints')
    p_gen.add_argument('--start',     type=int)
    p_gen.add_argument('--end',       type=int)
    p_gen.add_argument('--no-verify', action='store_true')

    # verify
    p_ver = sub.add_parser('verify', help='Verify and fix errors in posts and hints')
    p_ver.add_argument('--hints',   action='store_true')
    p_ver.add_argument('--posts',   action='store_true')
    p_ver.add_argument('--id',      type=int, nargs='+')
    p_ver.add_argument('--force',   action='store_true')
    p_ver.add_argument('--dry-run', action='store_true')

    # upload
    sub.add_parser('upload', help='Upload images to Telegram → image_ids.json')

    # deploy
    sub.add_parser('deploy', help='Deploy to Fly.io')

    args = parser.parse_args()

    dispatch = {
        'parse':    cmd_parse,
        'add':      cmd_add,
        'generate': cmd_generate,
        'verify':   cmd_verify,
        'upload':   cmd_upload,
        'deploy':   cmd_deploy,
    }
    dispatch[args.command](args)


if __name__ == '__main__':
    main()
