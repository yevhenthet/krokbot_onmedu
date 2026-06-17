#!/usr/bin/env python3
"""
Upload images from images/ folder to Telegram and save file_ids.
Run this whenever you add new images to the images/ folder.

Usage:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHANNEL_ID=... python3 upload_images.py
"""
import json, os, sys, requests
from pathlib import Path

token = os.environ.get('TELEGRAM_BOT_TOKEN')
if not token:
    print("Set TELEGRAM_BOT_TOKEN")
    sys.exit(1)

# Upload to your personal chat (not the channel) to avoid polluting it
cache_chat = 214742069

images_dir = Path('images')
ids_file = Path('image_ids.json')
image_ids = json.loads(ids_file.read_text()) if ids_file.exists() else {}

VALID_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}

new_count = 0
for img_path in sorted(images_dir.iterdir()):
    if img_path.suffix.lower() not in VALID_EXTS:
        continue
    qid = img_path.stem
    if qid in image_ids:
        print(f"id={qid}: вже завантажено, пропускаємо")
        continue

    with open(img_path, 'rb') as f:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendPhoto',
            data={'chat_id': cache_chat, 'caption': f'🖼 cache id={qid}'},
            files={'photo': f},
            timeout=30,
        )
    data = r.json()
    if data.get('ok'):
        file_id = data['result']['photo'][-1]['file_id']
        image_ids[qid] = file_id
        print(f"id={qid}: ✅ завантажено")
        new_count += 1
    else:
        print(f"id={qid}: ❌ помилка — {data.get('description')}")

ids_file.write_text(json.dumps(image_ids, indent=2, ensure_ascii=False))
print(f"\nГотово: {new_count} нових, {len(image_ids)} всього у image_ids.json")
