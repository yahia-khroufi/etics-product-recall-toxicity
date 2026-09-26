"""Point d'entrée FastAPI."""

from fastapi import FastAPI

app = FastAPI(title="Etics — Rappels toxicité")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
