import os
import json
import logging
import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="BIST Terminal v2")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# ─── API endpoint ───
@app.post("/api/analyze")
async def analyze(request: Request):
    try:
        body = await request.json()
        system = body.get("system", "")
        messages = body.get("messages", [])

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8000,
            "messages": messages,
        }
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                ANTHROPIC_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
            data = resp.json()

        if resp.status_code != 200:
            err_msg = data.get("error", {}).get("message", str(data))
            log.error(f"Anthropic {resp.status_code}: {err_msg}")
            return JSONResponse({"error": err_msg}, status_code=resp.status_code)

        return JSONResponse(data)
    except Exception as e:
        log.error(f"API error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── Health check ───
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0"}

# ─── Static files ───
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")

@app.get("/favicon.ico")
def favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon") if os.path.exists("static/favicon.ico") else JSONResponse({})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
