FROM python:3.12-slim

WORKDIR /srv/funquote

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATABASE_URL=sqlite:////srv/funquote/data/funquote.db \
    UPLOAD_DIR=/srv/funquote/data/uploads

VOLUME /srv/funquote/data
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
