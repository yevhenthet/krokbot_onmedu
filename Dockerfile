FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot.py .
COPY krok1_microbiology_mcqs.json .
COPY telegram_posts.json .
COPY image_ids.json .

CMD ["python3", "telegram_bot.py", "--schedule"]
