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

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_URL = "https://api.x.ai/v1/chat/completions"

# ─── API endpoint ───
@app.post("/api/analyze")
async def analyze(request: Request):
    try:
        body = await request.json()
        system = body.get("system", "")
        messages = body.get("messages", [])

        # Grok uses OpenAI format: system message goes in messages array
        grok_messages = []
        if system:
            grok_messages.append({"role": "system", "content": system})
        grok_messages.extend(messages)

        payload = {
            "model": "grok-3-fast",
            "max_tokens": 8000,
            "messages": grok_messages,
            "temperature": 0.3,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                XAI_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {XAI_API_KEY}",
                },
                json=payload,
            )
            data = resp.json()

        if resp.status_code != 200:
            err_msg = data.get("error", {}).get("message", str(data))
            log.error(f"Grok {resp.status_code}: {err_msg}")
            return JSONResponse({"error": err_msg}, status_code=resp.status_code)

        # Convert OpenAI format to our frontend format
        # OpenAI: {"choices": [{"message": {"content": "..."}}]}
        # Our frontend expects: {"content": [{"type": "text", "text": "..."}]}
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        result = {"content": [{"type": "text", "text": text}]}
        return JSONResponse(result)

    except Exception as e:
        log.error(f"API error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── Health check ───
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0", "engine": "grok"}

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
