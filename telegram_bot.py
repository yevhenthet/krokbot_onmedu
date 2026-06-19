#!/usr/bin/env python3
"""
Telegram quiz bot for KROK-1 microbiology.

Schedule:
  08:00 — quiz poll (question + options, quiz mode)
  10:00 — answer post (explanation with key facts)

Usage:
  # Test single question manually:
  python3 telegram_bot.py --test --id 1

  # Send today's poll (8:00 trigger):
  python3 telegram_bot.py --poll

  # Send today's answer (10:00 trigger):
  python3 telegram_bot.py --answer

  # Run scheduler (keeps running, sends daily):
  python3 telegram_bot.py --schedule
"""

import json, os, sys, argparse, time, re, random
from datetime import datetime, date
from pathlib import Path

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

# ── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')

POLL_HOUR   = 8
ANSWER_HOUR = 10

STATE_FILE  = Path('/data/bot_state.json')
POSTS_FILE  = Path('telegram_posts.json')
MCQS_FILE   = Path('krok1_microbiology_mcqs.json')
IMAGES_DIR   = Path('images')
IMAGE_IDS    = Path('image_ids.json')

# ── Helpers ──────────────────────────────────────────────────────────────────
def api(method, **kwargs):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, json=kwargs, timeout=30)
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(f"Telegram error: {data}")
    return data['result']


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'last_id': 0, 'poll_message_id': None, 'last_date': None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def load_data():
    with open(MCQS_FILE, encoding='utf-8') as f:
        qs = {q['id']: q for q in json.load(f)['questions']}
    with open(POSTS_FILE, encoding='utf-8') as f:
        posts = {r['id']: r['post'] for r in json.load(f)}
    return qs, posts


def next_question_id(state, qs):
    """Return next question ID after last_id, cycling back to 1."""
    all_ids = sorted(qs.keys())
    for qid in all_ids:
        if qid > state['last_id']:
            return qid
    return all_ids[0]  # cycle


def load_image_ids():
    if IMAGE_IDS.exists():
        return json.loads(IMAGE_IDS.read_text())
    return {}


def find_image(qid):
    """Return file_id string (from cache) or local Path, or None."""
    ids = load_image_ids()
    if str(qid) in ids:
        return ids[str(qid)]  # Telegram file_id string
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        p = IMAGES_DIR / f"{qid}.{ext}"
        if p.exists():
            return p  # fallback to local file
    return None


def send_image(qid):
    """Send image to channel using file_id or local file."""
    img = find_image(qid)
    if not img:
        return None
    if isinstance(img, str):
        # Use cached Telegram file_id
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            json={'chat_id': CHANNEL_ID, 'photo': img},
            timeout=30,
        )
    else:
        # Upload local file
        with open(img, 'rb') as f:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={'chat_id': CHANNEL_ID},
                files={'photo': f},
                timeout=30,
            )
    data = r.json()
    if not data.get('ok'):
        print(f"  ⚠️ Не вдалося відправити зображення: {data}")
        return None
    return data['result']['message_id']


def build_explanation(q, post_text):
    """Return hint from MCQ data, or fall back to first emoji bullet from post."""
    hint = q.get('hint', '').strip()
    if hint:
        return hint[:200]
    match = re.search(r'[🔬🧫💉⚠️🦠🔴🩸🧬🔵💊]\s*(.+)', post_text)
    if match:
        text = match.group(1).strip()
        return text[:197] + '...' if len(text) > 200 else text
    return ''


# ── Actions ──────────────────────────────────────────────────────────────────
def send_poll(qid=None, dry_run=False):
    qs, posts = load_data()
    state = load_state()

    if qid is None:
        qid = next_question_id(state, qs)

    q = qs[qid]
    post = posts.get(qid, '')
    opts = q['options']

    # Collect options, correct answer is always A in source
    correct_text = opts.get('A', '')
    all_options = [opts.get(k, '') for k in ('A', 'B', 'C', 'D', 'E') if opts.get(k) and opts.get(k) != '-']
    # Shuffle until correct answer is not at position 0 (too obvious)
    for _ in range(10):
        random.shuffle(all_options)
        if all_options.index(correct_text) != 0:
            break
    correct_option_id = all_options.index(correct_text)
    options = all_options
    explanation = build_explanation(q, post)

    print(f"📤 Відправляю POLL id={qid}: {q['question'][:60]}...")
    if dry_run:
        img = find_image(qid)
        print(f"  Зображення: {img or 'немає'}")
        print(f"  Варіанти: {options}")
        print(f"  Правильна: [{correct_option_id}] {options[correct_option_id]}")
        print(f"  Пояснення: {explanation}")
        print("  [dry-run — не відправляємо]")
        return

    # Send image first if exists
    if find_image(qid):
        send_image(qid)
        time.sleep(0.3)

    result = api(
        'sendPoll',
        chat_id=CHANNEL_ID,
        question=q['question'][:300],
        options=options,
        type='quiz',
        correct_option_id=correct_option_id,
        explanation=explanation,
        explanation_parse_mode='HTML',
        is_anonymous=True,
    )

    state['last_id'] = qid
    state['poll_message_id'] = result['message_id']
    state['last_date'] = date.today().isoformat()
    save_state(state)
    print(f"  ✅ Poll відправлено (message_id={result['message_id']})")


TAXA = [
    'Escherichia', 'Salmonella', 'Shigella', 'Staphylococcus', 'Streptococcus',
    'Clostridium', 'Corynebacterium', 'Mycobacterium', 'Neisseria', 'Treponema',
    'Borrelia', 'Leptospira', 'Vibrio', 'Campylobacter', 'Helicobacter',
    'Pseudomonas', 'Klebsiella', 'Proteus', 'Yersinia', 'Francisella',
    'Brucella', 'Bacillus', 'Listeria', 'Enterococcus', 'Haemophilus',
    'Bordetella', 'Legionella', 'Rickettsia', 'Chlamydia', 'Mycoplasma',
    'Candida', 'Aspergillus', 'Cryptococcus', 'Actinomyces', 'Nocardia',
    'Plasmodium', 'Toxoplasma', 'Trichomonas', 'Leishmania', 'Trypanosoma',
    'Enterobacter', 'Serratia', 'Citrobacter', 'Acinetobacter', 'Stenotrophomonas',
]
# Regex: known genus (optionally followed by a lowercase species epithet)
# Also matches abbreviated forms like "E. coli", "S. aureus"
_TAXA_RE = re.compile(
    r'\b(' + '|'.join(TAXA) + r')(\s+[a-z][a-z]+)?\b'
    r'|'
    r'\b([A-Z]\.\s+[a-z][a-z]+)\b'
)

def italicize_taxa(text):
    """Wrap scientific taxon names (full and abbreviated) with HTML italic tags."""
    def replace(m):
        return f"<i>{m.group(0)}</i>"
    return _TAXA_RE.sub(replace, text)


def html_escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_answer(text):
    """Add blank lines between answer sections for proper Telegram spacing."""
    # Normalize leading spaces on each line
    lines = [l.strip() for l in text.splitlines()]
    result = []
    for line in lines:
        # Add blank line before КЛЮЧОВІ СЛОВА and before emoji bullet points
        if line.startswith('КЛЮЧОВІ СЛОВА') and result and result[-1] != '':
            result.append('')
        if line and line[0] in '🔬🧫💉⚠️🦠🔴🩸🧬🔵💊🦷🫁🫀🧲🔵' and result and result[-1] != '':
            # only add blank line before the first bullet
            if not any(r and r[0] in '🔬🧫💉⚠️🦠🔴🩸🧬🔵💊🦷🫁🫀🧲🔵' for r in result[-3:]):
                result.append('')
        result.append(line)
    return '\n'.join(result)


def extract_answer_section(post):
    """Return only the answer+key facts part (everything from ✅ onwards)."""
    match = re.search(r'(✅\s*Відповідь.+)', post, re.DOTALL)
    return match.group(1).strip() if match else post


def send_answer(qid=None, dry_run=False):
    qs, posts = load_data()
    state = load_state()

    if qid is None:
        qid = state.get('last_id')
        if not qid:
            print("❌ Немає збереженого ID — спочатку запусти --poll")
            return

    post = posts.get(qid, '')
    if not post:
        print(f"❌ Пост для id={qid} не знайдено")
        return

    post = extract_answer_section(post)

    post_html = italicize_taxa(html_escape(format_answer(post)))

    print(f"📤 Відправляю ANSWER id={qid}...")
    if dry_run:
        print(post_html[:300] + '...')
        print("  [dry-run — не відправляємо]")
        return

    api(
        'sendMessage',
        chat_id=CHANNEL_ID,
        text=post_html,
        parse_mode='HTML',
        disable_web_page_preview=True,
    )
    print(f"  ✅ Відповідь відправлена")


def run_scheduler():
    if not HAS_SCHEDULER:
        print("❌ Встанови APScheduler: pip install apscheduler")
        sys.exit(1)

    scheduler = BlockingScheduler(timezone='Europe/Kyiv')
    scheduler.add_job(send_poll,   'cron', hour=POLL_HOUR,   minute=0, misfire_grace_time=300)
    scheduler.add_job(send_answer, 'cron', hour=ANSWER_HOUR, minute=0, misfire_grace_time=300)

    print(f"🕐 Scheduler запущено (Kyiv time)")
    print(f"   Poll:   {POLL_HOUR:02d}:00")
    print(f"   Answer: {ANSWER_HOUR:02d}:00")
    print("   Ctrl+C для зупинки")
    scheduler.start()


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--poll',     action='store_true', help='Send today\'s quiz poll')
    parser.add_argument('--answer',   action='store_true', help='Send today\'s answer post')
    parser.add_argument('--schedule', action='store_true', help='Run daily scheduler')
    parser.add_argument('--test',     action='store_true', help='Dry-run without sending')
    parser.add_argument('--id',       type=int,            help='Specific question ID')
    args = parser.parse_args()

    if not BOT_TOKEN:
        print("❌ Встанови: export TELEGRAM_BOT_TOKEN=...")
        sys.exit(1)
    if not CHANNEL_ID and not args.test:
        print("❌ Встанови: export TELEGRAM_CHANNEL_ID=...")
        sys.exit(1)

    if args.schedule:
        run_scheduler()
    elif args.poll:
        send_poll(qid=args.id, dry_run=args.test)
    elif args.answer:
        send_answer(qid=args.id, dry_run=args.test)
    elif args.test:
        qid = args.id or 1
        print(f"=== DRY RUN id={qid} ===")
        send_poll(qid=qid, dry_run=True)
        print()
        send_answer(qid=qid, dry_run=True)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
