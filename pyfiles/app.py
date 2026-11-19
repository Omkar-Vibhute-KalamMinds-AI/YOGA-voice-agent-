"""Web API for YOGA Voice Agent. The CLI in lg_chatbot.py stays available."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import openai
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import lg_chatbot
import voice

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
titles: dict[str, str] = {}

class Decision(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=12)
    record: str | None = Field(default=None, max_length=8000)

class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=80)
    message: str = Field(default="", max_length=8000)
    user_id: str = Field(default=lg_chatbot.USER_ID, min_length=1, max_length=40)
    language: str = Field(default="", max_length=16)
    replace_user_index: int | None = Field(default=None, ge=0)
    decisions: list[Decision] | None = None


def api_error_text(exc: openai.APIError) -> str:
    detail = getattr(exc, "body", None) or exc
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return str((detail[0].get("error") or {}).get("message", detail))
    if isinstance(detail, dict):
        return str((detail.get("error") or {}).get("message", detail))
    return str(detail)


def thread_title(thread_id: str) -> str:
    if thread_id in titles:
        return titles[thread_id]
    for row in lg_chatbot.thread_history(thread_id):
        if row["role"] == "User" and row["content"]:
            text = row["content"].strip()
            return text if len(text) <= 42 else text[:42] + "…"
    return "New chat"


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_task = (
        asyncio.create_task(voice.warm_previews("en-IN"))
        if (os.getenv("SARVAM_API_KEY") or "").strip()
        else None
    )
    with lg_chatbot.open_memory_store() as memory_store:
        lg_chatbot.store = memory_store
        await lg_chatbot.setup()
        yield
    if warm_task is not None:
        warm_task.cancel()
    lg_chatbot.store = None
    lg_chatbot.chatbot = None


app = FastAPI(title="YOGA Voice Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "name": "YOGA Voice Agent",
        "stt": voice.voice_stt_provider(),
        "tts": voice.voice_tts_provider(),
    }


@app.get("/api/threads")
def list_threads() -> dict[str, list[dict[str, str]]]:
    rows = [
        {"id": thread_id, "title": thread_title(thread_id)}
        for thread_id in lg_chatbot.saved_threads()
    ]
    return {"threads": rows}


@app.post("/api/threads")
def new_thread() -> dict[str, str]:
    thread_id = str(uuid.uuid4())
    titles[thread_id] = "New chat"
    return {"id": thread_id, "title": titles[thread_id]}


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str) -> dict:
    if thread_id not in lg_chatbot.saved_threads() and thread_id not in titles:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {
        "id": thread_id,
        "title": thread_title(thread_id),
        "messages": lg_chatbot.thread_history(thread_id) if thread_id in lg_chatbot.saved_threads() else [],
    }


@app.post("/api/chat")
async def chat(body: ChatRequest) -> StreamingResponse:
    message = body.message.strip()
    decisions = [item.model_dump() for item in body.decisions] if body.decisions else None
    if decisions is None and not message:
        raise HTTPException(status_code=400, detail="Type a message.")
    user_id = lg_chatbot.normalize_user_id(body.user_id)
    if decisions is None and body.replace_user_index is not None:
        try:
            lg_chatbot.rewind_before_user_turn(body.thread_id, body.replace_user_index)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if decisions is None:
        if body.thread_id not in titles:
            titles[body.thread_id] = message if len(message) <= 42 else message[:42] + "…"
        elif titles[body.thread_id] == "New chat":
            titles[body.thread_id] = message if len(message) <= 42 else message[:42] + "…"

    async def events():
        try:
            async for event in lg_chatbot.stream_reply(
                body.thread_id,
                message,
                user_id,
                resume=decisions,
                language=body.language,
            ):
                if event["type"] == "text":
                    yield f"data: {json.dumps({'text': event['text']})}\n\n"
                elif event["type"] == "reply_done":
                    yield f"data: {json.dumps({'reply_done': True})}\n\n"
                elif event["type"] == "interrupt":
                    yield f"data: {json.dumps({'interrupt': event['requests']})}\n\n"
                    return
            yield f"data: {json.dumps({'done': True, 'title': titles.get(body.thread_id, 'New chat')})}\n\n"
        except openai.APIError as exc:
            yield f"data: {json.dumps({'error': api_error_text(exc)})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


class SpokenRequest(BaseModel):
    text: str = Field(default="", max_length=8000)
    thread_id: str = Field(default="", max_length=80)
    user_id: str = Field(default=lg_chatbot.USER_ID, min_length=1, max_length=40)
    language: str = Field(default="", max_length=16)
    speaker: str = Field(default="", max_length=24)


@app.post("/api/voice/transcribe")
async def transcribe_voice(
    audio: UploadFile = File(...),
    thread_id: str = Form(default=""),
    user_id: str = Form(default=lg_chatbot.USER_ID),
    language: str = Form(default="unknown"),
) -> dict[str, str]:
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty recording.")
    try:
        result = voice.transcribe_recording(
            audio=data,
            mime=audio.content_type or "audio/wav",
            filename=audio.filename or "speech.wav",
            thread_id=thread_id,
            user_id=lg_chatbot.normalize_user_id(user_id),
            language=language,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "transcript": result.get("transcript") or "",
        "language": result.get("language") or language,
    }

@app.post("/api/voice/speak-stream")
async def speak_stream(body: SpokenRequest) -> StreamingResponse:
    spoken = body.text.strip()
    if not spoken:
        raise HTTPException(status_code=400, detail="Nothing to speak.")

    async def chunks():
        async for chunk in voice.iter_pcm(spoken, body.language, body.speaker):
            yield chunk 

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={"X-Audio-Rate": str(voice.PCM_RATE), "Cache-Control": "no-store"},
    )


class WarmRequest(BaseModel):
    language: str = Field(default="en-IN", max_length=16)
    speaker: str = Field(default="", max_length=24)


_warm_task: asyncio.Task | None = None


@app.post("/api/voice/warm")
async def warm_voices(body: WarmRequest) -> dict[str, str]:
    global _warm_task
    if _warm_task is not None and not _warm_task.done():
        _warm_task.cancel()
    _warm_task = asyncio.create_task(voice.warm_previews(body.language, body.speaker))
    return {"status": "warming"}


@app.websocket("/api/voice/live")
async def live_voice(websocket: WebSocket) -> None:
    await websocket.accept()
    stream: voice.SarvamStream | None = None
    reader: asyncio.Task | None = None
    try:
        message = json.loads(await websocket.receive_text())
        if message.get("type") != "start":
            await websocket.close()
            return
        stream = voice.SarvamStream(str(message.get("language") or ""), str(message.get("speaker") or ""))
        opening = str(message.get("text") or "")
        await stream.open(opening)
        await stream.push_full(opening)

        async def relay() -> None:
            assert stream is not None
            async for chunk in stream.iter_audio():
                await websocket.send_bytes(chunk)

        reader = asyncio.create_task(relay())
        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("type") == "text":
                await stream.push_full(str(message.get("text") or ""))
            elif message.get("type") == "end":
                await stream.finish()
                break
        await reader
    except WebSocketDisconnect:
        pass
    finally:
        if reader is not None:
            reader.cancel()
        if stream is not None:
            await stream.close()


@app.post("/api/voice/speak")
async def speak_reply(body: SpokenRequest) -> Response:
    spoken = body.text.strip()
    if not spoken:
        raise HTTPException(status_code=400, detail="Nothing to speak.")
    try:
        result = await asyncio.to_thread(
            voice.synthesize_reply,
            spoken,
            body.thread_id,
            lg_chatbot.normalize_user_id(body.user_id),
            body.language,
            body.speaker,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audio = result.get("audio") or b""
    if not audio:
        raise HTTPException(status_code=502, detail="TTS returned no audio.")
    return Response(content=audio, media_type="audio/wav")


@app.post("/api/voice/spoken")
async def spoken_reply(body: SpokenRequest) -> dict[str, str]:
    voice.trace_spoken_reply(
        text=body.text,
        thread_id=body.thread_id,
        user_id=lg_chatbot.normalize_user_id(body.user_id),
    )
    return {"status": "logged"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    ) 
