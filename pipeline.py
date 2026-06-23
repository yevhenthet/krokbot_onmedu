#!/usr/bin/env python3
"""
Single entry point for the KROK-1 bot content pipeline.

Commands:
  parse    — Parse Excel/text file → krok1_microbiology_mcqs.json
  generate — Generate posts + hints via Claude API
  verify   — Verify and fix errors in posts and hints
  upload   — Upload images to Telegram → image_ids.json
  deploy   — Deploy to Fly.io

Usage:
  source venv/bin/activate

  # Full pipeline (new question set):
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py parse --input file.xlsx
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py generate
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify
  TELEGRAM_BOT_TOKEN=... python3 pipeline.py upload
  python3 pipeline.py deploy

  # Fix errors in specific questions:
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --id 6 7 8

  # Re-check all posts (force):
  ANTHROPIC_API_KEY=sk-... python3 pipeline.py verify --posts --force
"""

import argparse, subprocess, sys, os
from pathlib import Path


def run(cmd: list[str], **kwargs):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)


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
        # default: both
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
    p_parse = sub.add_parser('parse', help='Parse Excel/text → JSON')
    p_parse.add_argument('--input',  required=True, help='Input file (.xlsx, .txt)')
    p_parse.add_argument('--output', default='krok1_microbiology_mcqs.json')
    p_parse.add_argument('--claude', action='store_true', help='Force Claude parser')

    # generate
    p_gen = sub.add_parser('generate', help='Generate posts + hints')
    p_gen.add_argument('--start',     type=int)
    p_gen.add_argument('--end',       type=int)
    p_gen.add_argument('--no-verify', action='store_true')

    # verify
    p_ver = sub.add_parser('verify', help='Verify and fix errors')
    p_ver.add_argument('--hints',   action='store_true')
    p_ver.add_argument('--posts',   action='store_true')
    p_ver.add_argument('--id',      type=int, nargs='+')
    p_ver.add_argument('--force',   action='store_true')
    p_ver.add_argument('--dry-run', action='store_true')

    # upload
    sub.add_parser('upload', help='Upload images → image_ids.json')

    # deploy
    sub.add_parser('deploy', help='Deploy to Fly.io')

    args = parser.parse_args()

    dispatch = {
        'parse':    cmd_parse,
        'generate': cmd_generate,
        'verify':   cmd_verify,
        'upload':   cmd_upload,
        'deploy':   cmd_deploy,
    }
    dispatch[args.command](args)


if __name__ == '__main__':
    main()
