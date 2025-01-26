FROM python:3.11-alpine
LABEL authors="tosha"

WORKDIR /app

RUN pip install pyTelegramBotAPI psycopg2-binary

COPY nails_bot.py /app/nails_bot.py

EXPOSE 5000

ENTRYPOINT ["python", "nails_bot.py"]