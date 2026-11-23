import sys
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

# 把项目根目录加入路径，以便导入 backend 里的代码
sys.path.append(str(Path(__file__).resolve().parent.parent))

# 直接复用 backend/main.py 里的核心内容
from backend.main import (
    VOICES, VOICE_IDS, MAX_TEXT_LENGTH,
    SynthesizeRequest,
    normalize_rate, normalize_pitch, normalize_volume,
    synthesize_mp3
)

app = FastAPI()

@app.get("/api/voices")
async def list_voices():
    return {"voices": VOICES}

@app.post("/api/synthesize")
async def synthesize(req: SynthesizeRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail=f"文本太长了，请控制在 {MAX_TEXT_LENGTH} 字以内")
    if req.voice not in VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"不支持的音色：{req.voice}")

    rate = normalize_rate(req.rate)
    pitch = normalize_pitch(req.pitch)
    volume = normalize_volume(req.volume)

    try:
        audio = await synthesize_mp3(text, req.voice, rate, pitch, volume)
    except Exception:
        raise HTTPException(status_code=502, detail="语音服务暂时不可用，请稍后重试")

    filename = f"TTS_{datetime.now():%Y%m%d_%H%M%S}.mp3"
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@app.post("/api/ping")
def ping():
    return {"ok": True}

@app.post("/api/bye")
def bye():
    return {"ok": True}
