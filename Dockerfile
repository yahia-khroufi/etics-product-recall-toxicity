FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 etics && useradd --uid 1000 --gid 1000 --create-home etics
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=etics:etics . .
RUN mkdir -p data/raw data/extracted data/processed backups runtime \
    && chown -R etics:etics data backups runtime
USER etics
EXPOSE 8501
ENTRYPOINT ["python", "-m", "src.container"]
CMD ["dashboard"]
