FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ config/
COPY prompts/ prompts/
COPY word_agent/ word_agent/
COPY frontend/ frontend/

RUN mkdir -p out/cache

EXPOSE 8000

CMD ["uvicorn", "word_agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
