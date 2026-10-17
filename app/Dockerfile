FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/shop.db

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --no-create-home app
COPY . .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

RUN mkdir -p /app/data && chown app:app /app/data


USER app
EXPOSE 5000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]