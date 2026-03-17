import os
import logging
import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="BistBull v2")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

log.info(f"OPENAI_API_KEY set: {'YES' if OPENAI_API_KEY else 'NO'}, len={len(OPENAI_API_KEY)}, model={MODEL}")


@app.post("/api/analyze")
async def analyze(request: Request):
    try:
        body = await request.json()
        system = body.get("system", "")
        messages = body.get("messages", [])

        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)

        payload = {
            "model": MODEL,
            "max_tokens": 4096,
            "temperature": 0.3,
            "messages": oai_messages,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                OPENAI_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                },
                json=payload,
            )

        raw = resp.text
        log.info(f"OpenAI status={resp.status_code} body={raw[:300]}")

        try:
            data = resp.json()
        except Exception:
            return JSONResponse({"error": f"Parse error: {raw[:200]}"}, status_code=500)

        if resp.status_code != 200:
            if isinstance(data, dict):
                err = data.get("error", {})
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            else:
                msg = str(data)
            log.error(f"OpenAI {resp.status_code}: {msg}")
            return JSONResponse({"error": msg}, status_code=resp.status_code)

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return JSONResponse({"content": [{"type": "text", "text": text}]})

    except Exception as e:
        log.error(f"API error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0", "engine": MODEL}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
def favicon():
    if os.path.exists("static/favicon.ico"):
        return FileResponse("static/favicon.ico", media_type="image/x-icon")
    return JSONResponse({})


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
