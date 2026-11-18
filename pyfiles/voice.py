"""Speech in and speech out for the Voice Agent. Each call is a LangSmith run."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tracing import traceable, voice_inputs, voice_outputs

NVIDIA_STT_URL = (
    os.getenv("NVIDIA_STT_URL")
    or "https://1598d209-5e27-4d3c-8079-4751568b1081.invocation.api.nvcf.nvidia.com/v1/audio/transcriptions"
)
NVIDIA_STT_MODEL = os.getenv("NVIDIA_STT_MODEL") or "nvidia/parakeet-ctc-1.1b-asr"
NVIDIA_STT_LANGUAGE = os.getenv("NVIDIA_STT_LANGUAGE") or "en-US"
NVIDIA_TTS_URL = (
    os.getenv("NVIDIA_TTS_URL")
    or "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com/v1/audio/synthesize"
)
NVIDIA_TTS_VOICE = os.getenv("NVIDIA_TTS_VOICE") or "Magpie-Multilingual.EN-US.Aria"
NVIDIA_TTS_LANGUAGE = os.getenv("NVIDIA_TTS_LANGUAGE") or "en-US"

SARVAM_STT_URL = os.getenv("SARVAM_STT_URL") or "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = os.getenv("SARVAM_TTS_URL") or "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_MODEL = os.getenv("SARVAM_STT_MODEL") or "saaras:v4"
SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL") or "bulbul:v3"

# Names on the left are what the voice picker sends.
# Bulbul v3 only accepts the lowercase ids on the right, and unknown
# names are rejected or spoken by the default female voice.
FEMALE_SPEAKERS = {
    "arya": "priya",
    "vani": "ritu",
    "dhwani": "neha",
    "nirali": "pooja",
    "rohini": "simran",
    "maya": "ishita",
    "priya": "priya",
    "ritu": "ritu",
    "neha": "neha",
    "pooja": "pooja",
    "simran": "simran",
    "ishita": "ishita",
    "anushka": "priya",
    "manisha": "ritu",
    "vidya": "neha",
}
MALE_SPEAKERS = {
    "varun": "varun",
    "indra": "shubh",
    "krishna": "aditya",
    "vayu": "rahul",
    "kartik": "amit",
    "shubh": "shubh",
    "aditya": "aditya",
    "rahul": "rahul",
    "ratan": "ratan",
    "amit": "amit",
    "abhilash": "shubh",
    "karun": "aditya",
    "hitesh": "rahul",
}
SARVAM_TTS_SPEAKERS = set(FEMALE_SPEAKERS) | set(MALE_SPEAKERS)

SARVAM_TTS_LANGUAGES = {
    "unknown",
    "en-IN",
    "hi-IN",
    "bn-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
    "gu-IN",
}


def _nvidia_key() -> str:
    key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set.")
    return key


def _sarvam_key() -> str:
    key = (os.getenv("SARVAM_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "SARVAM_API_KEY is not set. Create a key at https://dashboard.sarvam.ai and add it to .env."
        )
    return key


def voice_stt_provider() -> str:
    chosen = (os.getenv("VOICE_STT_PROVIDER") or "").strip().lower()
    if chosen:
        return chosen
    return "sarvam" if (os.getenv("SARVAM_API_KEY") or "").strip() else "nvidia"


def voice_tts_provider() -> str:
    chosen = (os.getenv("VOICE_TTS_PROVIDER") or "").strip().lower()
    if chosen:
        return chosen
    return "sarvam" if (os.getenv("SARVAM_API_KEY") or "").strip() else "nvidia"


HINDI_MARKERS = (
    " हूँ", " हूं", " हैं", " है।", " है?", " है ", " नहीं", " कैसे", " क्या", " मैं ",
    " आप ", " गया", " गई", " था ", " थी", " होगा", " चाहिए", " बहुत", " यह ", " वह ",
    " कर सकते",
)
MARATHI_MARKERS = (
    " आहे", " आहोत", " आहात", " तुम्ही", " मला", " आम्ही", " नाही", " करतो", " करते",
    " काय", " कसे", " होय", " नको", " पाहिजे", " तुझा", " तुमचा",
)


def _count_markers(text: str, markers: tuple[str, ...]) -> int:
    return sum(text.count(marker) for marker in markers)


def _hindi_or_marathi(text: str, hint: str) -> str:
    hindi = _count_markers(text, HINDI_MARKERS)
    marathi = _count_markers(text, MARATHI_MARKERS)
    if marathi > hindi:
        return "mr-IN"
    return "hi-IN"


def infer_language_from_text(text: str, hint: str = "unknown") -> str:
    """Guess a Sarvam language code from native script, then the STT hint."""
    hinted = normalize_language(hint)
    counts: dict[str, int] = {
        "hi-IN": 0,
        "bn-IN": 0,
        "pa-IN": 0,
        "gu-IN": 0,
        "od-IN": 0,
        "ta-IN": 0,
        "te-IN": 0,
        "kn-IN": 0,
        "ml-IN": 0,
        "en-IN": 0,
    }
    for char in text or "":
        code = ord(char)
        if 0x0900 <= code <= 0x097F:
            counts["hi-IN"] += 1
        elif 0x0980 <= code <= 0x09FF:
            counts["bn-IN"] += 1
        elif 0x0A00 <= code <= 0x0A7F:
            counts["pa-IN"] += 1
        elif 0x0A80 <= code <= 0x0AFF:
            counts["gu-IN"] += 1
        elif 0x0B00 <= code <= 0x0B7F:
            counts["od-IN"] += 1
        elif 0x0B80 <= code <= 0x0BFF:
            counts["ta-IN"] += 1
        elif 0x0C00 <= code <= 0x0C7F:
            counts["te-IN"] += 1
        elif 0x0C80 <= code <= 0x0CFF:
            counts["kn-IN"] += 1
        elif 0x0D00 <= code <= 0x0D7F:
            counts["ml-IN"] += 1
        elif "A" <= char <= "Z" or "a" <= char <= "z":
            counts["en-IN"] += 1
    script = max(counts, key=lambda key: counts[key])
    if counts[script] == 0:
        return hinted if hinted != "unknown" else "unknown"
    if script == "hi-IN":
        return _hindi_or_marathi(text, hinted)
    if hinted != "unknown" and hinted != "en-IN" and script == "en-IN":
        return hinted
    return script


def normalize_language(code: str) -> str:
    text = (code or "").strip()
    if not text or text.lower() in {"auto", "unknown", "und"}:
        return "unknown"
    aliases = {
        "en": "en-IN",
        "en-us": "en-IN",
        "english": "en-IN",
        "hindi": "hi-IN",
        "marathi": "mr-IN",
        "bengali": "bn-IN",
        "tamil": "ta-IN",
        "telugu": "te-IN",
        "kannada": "kn-IN",
        "malayalam": "ml-IN",
        "punjabi": "pa-IN",
        "gujarati": "gu-IN",
        "odia": "od-IN",
        "oriya": "od-IN",
        "hi": "hi-IN",
        "bn": "bn-IN",
        "kn": "kn-IN",
        "ml": "ml-IN",
        "mr": "mr-IN",
        "or": "od-IN",
        "od": "od-IN",
        "pa": "pa-IN",
        "ta": "ta-IN",
        "te": "te-IN",
        "gu": "gu-IN",
    }
    lowered = text.lower()
    if lowered in aliases:
        return aliases[lowered]
    if len(text) == 2:
        return aliases.get(lowered, "unknown")
    return text


def _form(fields: dict[str, str], filename: str = "", audio: bytes = b"", mime: str = "") -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    if audio:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
        )
        chunks.append(audio)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


TTS_MAX_CHARS = 2500
PCM_RATE = 22050


def _spoken_text(text: str) -> str:
    spoken = (text or "").replace("**", "").strip()
    return spoken[:TTS_MAX_CHARS]


def _http(request: Request, label: str) -> bytes:
    try:
        with urlopen(request, timeout=90) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"{label} failed ({exc.code}): {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"{label} is unreachable: {exc.reason}") from exc


def _read_transcript(payload) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return str(payload).strip()
    if payload.get("transcript"):
        return str(payload["transcript"]).strip()
    if payload.get("text"):
        return str(payload["text"]).strip()
    transcripts = payload.get("transcripts") or payload.get("results") or []
    if transcripts and isinstance(transcripts, list):
        first = transcripts[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("transcript") or "").strip()
        return str(first).strip()
    return ""


def _read_language(payload, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    for key in ("language_code", "language", "request_language_code"):
        value = payload.get(key)
        if value:
            return normalize_language(str(value))
    return normalize_language(fallback)


def _sarvam_transcribe(audio: bytes, filename: str, mime: str, language: str) -> tuple[str, str]:
    fields = {
        "model": SARVAM_STT_MODEL,
        "mode": "transcribe",
        "language_code": language if language != "unknown" else "unknown",
    }
    body, content_type = _form(fields, filename or "speech.wav", audio, mime or "audio/wav")
    request = Request(
        SARVAM_STT_URL,
        data=body,
        method="POST",
        headers={
            "api-subscription-key": _sarvam_key(),
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )
    raw = _http(request, "Sarvam STT").decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), language
    return _read_transcript(payload), _read_language(payload, language)


def _nvcf_transcribe(audio: bytes, filename: str, mime: str, language: str) -> tuple[str, str]:
    body, content_type = _form(
        {"language": language if language not in {"unknown", "en-IN"} else NVIDIA_STT_LANGUAGE},
        filename,
        audio,
        mime or "audio/wav",
    )
    request = Request(
        NVIDIA_STT_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_nvidia_key()}",
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )
    raw = _http(request, "NVIDIA ASR").decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), language
    return _read_transcript(payload), _read_language(payload, language)


def _decode_sarvam_audio(payload: dict) -> bytes:
    audios = payload.get("audios") or payload.get("audio")
    chunk = audios[0] if isinstance(audios, list) and audios else audios
    if not isinstance(chunk, str) or not chunk.strip():
        raise RuntimeError("Sarvam TTS returned no audio.")
    return base64.b64decode(chunk)


def normalize_speaker(speaker: str) -> str:
    name = (speaker or "").strip().lower()
    if name in MALE_SPEAKERS:
        return MALE_SPEAKERS[name]
    if name in FEMALE_SPEAKERS:
        return FEMALE_SPEAKERS[name]
    return "priya"


def _sarvam_synthesize(text: str, language: str, speaker: str = "") -> bytes:
    spoken = _spoken_text(text)
    if not spoken:
        return b""
    lang = infer_language_from_text(text, language)
    if lang not in SARVAM_TTS_LANGUAGES or lang == "unknown":
        lang = "en-IN" if infer_language_from_text(text, "unknown") == "en-IN" else "hi-IN"
    body = json.dumps(
        {
            "text": spoken,
            "model": SARVAM_TTS_MODEL,
            "speaker": normalize_speaker(speaker),
            "language_code": lang,
            "speech_sample_rate": 22050,
        }
    ).encode()
    request = Request(
        SARVAM_TTS_URL,
        data=body,
        method="POST",
        headers={
            "api-subscription-key": _sarvam_key(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Sarvam TTS failed ({exc.code}): {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Sarvam TTS is unreachable: {exc.reason}") from exc
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        return _decode_sarvam_audio(payload)
    return raw


def _nvcf_synthesize(text: str, language: str, speaker: str = "") -> bytes:
    spoken = _spoken_text(text)
    if not spoken:
        return b""
    female = (speaker or "").strip().lower() in FEMALE_SPEAKERS
    voice = NVIDIA_TTS_VOICE if female else os.getenv("NVIDIA_TTS_VOICE_MALE") or "Magpie-Multilingual.EN-US.John"
    body, content_type = _form(
        {
            "text": spoken,
            "language": language if language.startswith("en") else NVIDIA_TTS_LANGUAGE,
            "voice": voice,
            "sample_rate_hz": "22050",
            "encoding": "LINEAR_PCM",
        }
    )
    request = Request(
        NVIDIA_TTS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_nvidia_key()}",
            "Accept": "audio/wav, application/octet-stream, */*",
            "Content-Type": content_type,
        },
    )
    return _http(request, "NVIDIA TTS")


@traceable(
    name="voice_transcribe",
    run_type="chain",
    tags=["voice", "stt", "voice-agent"],
    process_inputs=voice_inputs,
    process_outputs=voice_outputs,
)
def transcribe_recording(
    audio: bytes,
    mime: str = "audio/wav",
    filename: str = "speech.wav",
    thread_id: str = "",
    user_id: str = "",
    language: str = "unknown",
) -> dict:
    """Turn a microphone recording into text. The audio bytes stay off the trace."""
    language = normalize_language(language)
    if not audio:
        return {
            "transcript": "",
            "language": language,
            "model": SARVAM_STT_MODEL if voice_stt_provider() == "sarvam" else NVIDIA_STT_MODEL,
            "thread_id": thread_id,
            "user_id": user_id,
        }
    provider = voice_stt_provider()
    if provider == "sarvam":
        text, detected = _sarvam_transcribe(audio, filename or "speech.wav", mime or "audio/wav", language)
        model = SARVAM_STT_MODEL
    else:
        text, detected = _nvcf_transcribe(audio, filename or "speech.wav", mime or "audio/wav", language)
        model = NVIDIA_STT_MODEL
    detected = infer_language_from_text(text, detected)
    return {
        "transcript": text,
        "language": detected,
        "model": model,
        "provider": provider,
        "bytes": len(audio),
        "mime": mime,
        "thread_id": thread_id,
        "user_id": user_id,
    }


@traceable(
    name="voice_speak",
    run_type="chain",
    tags=["voice", "tts", "voice-agent"],
    process_inputs=voice_inputs,
    process_outputs=voice_outputs,
)
def synthesize_reply(
    text: str,
    thread_id: str = "",
    user_id: str = "",
    language: str = "unknown",
    speaker: str = "",
) -> dict:
    """Turn the agent reply into spoken audio. Audio bytes stay off the trace."""
    spoken = _spoken_text(text)
    language = infer_language_from_text(spoken, normalize_language(language))
    speaker = normalize_speaker(speaker)
    provider = voice_tts_provider()
    if provider == "sarvam":
        audio = _sarvam_synthesize(spoken, language, speaker) if spoken else b""
        model = SARVAM_TTS_MODEL
    else:
        audio = _nvcf_synthesize(spoken, language, speaker) if spoken else b""
        model = "magpie-tts-multilingual"
    return {
        "text": spoken,
        "chars": len(spoken),
        "audio": audio,
        "bytes": len(audio),
        "model": model,
        "provider": provider,
        "language": language,
        "speaker": speaker,
        "thread_id": thread_id,
        "user_id": user_id,
    }

#------------------------------------------------------------------------------------------------   

_pcm_cache: dict[tuple[str, str, str], bytes] = {}
_pcm_inflight: dict[tuple[str, str, str], asyncio.Future[bytes]] = {}
_pcm_gate = asyncio.Lock()

PREVIEW_SPEAKERS = (
    ("arya", "Arya"),
    ("vani", "Vani"),
    ("dhwani", "Dhwani"),
    ("nirali", "Nirali"),
    ("rohini", "Rohini"),
    ("maya", "Maya"),
    ("varun", "Varun"),
    ("indra", "Indra"),
    ("krishna", "Krishna"),
    ("vayu", "Vayu"),
    ("kartik", "Kartik"),
)


def preview_line(language: str, name: str) -> str:
    """Keep these sentences identical to the greetings in web/app.js."""
    lines = {
        "en-IN": f"Hello, my name is {name}, how can I help you today.",
        "hi-IN": f"नमस्ते, मेरा नाम {name} है, आज मैं आपकी कैसे मदद करूँ।",
        "mr-IN": f"नमस्कार, माझं नाव {name} आहे, आज मी तुम्हाला कशी मदत करू?",
        "te-IN": f"హలో, నా పేరు {name}, ఈ రోజు నేను మీకు ఎలా సహాయం చేయగలను.",
        "kn-IN": f"ನಮಸ್ಕಾರ, ನನ್ನ ಹೆಸರು {name}, ಇಂದು ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ.",
        "ml-IN": f"ഹലോ, എന്റെ പേര് {name}, ഇന്ന് ഞാൻ നിങ്ങളെ എങ്ങനെ സഹായിക്കും.",
        "ta-IN": f"வணக்கம், என் பெயர் {name}, இன்று நான் உங்களுக்கு எப்படி உதவ முடியும்.",
        "pa-IN": f"ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਮੇਰਾ ਨਾਂ {name} ਹੈ, ਅੱਜ ਮੈਂ ਤੁਹਾਡੀ ਕਿਵੇਂ ਮਦਦ ਕਰਾਂ.",
        "od-IN": f"ନମସ୍କାର, ମୋ ନାମ {name}, ଆଜି ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି.",
        "bn-IN": f"নমস্কার, আমার নাম {name}, আজ আমি আপনাকে কীভাবে সাহায্য করতে পারি.",
        "gu-IN": f"નમસ્તે, મારું નામ {name} છે, આજે હું તમને કેવી રીતે મદદ કરી શકું.",
    }
    code = language if language in lines else "en-IN"
    return lines[code]


def _tts_language(text: str, language: str) -> str:
    lang = infer_language_from_text(text, normalize_language(language))
    if lang not in SARVAM_TTS_LANGUAGES or lang == "unknown":
        lang = "en-IN" if infer_language_from_text(text, "unknown") == "en-IN" else "hi-IN"
    return lang


def _pcm_key(text: str, language: str, speaker: str) -> tuple[str, str, str]:
    spoken = _spoken_text(text)
    return (normalize_speaker(speaker), _tts_language(spoken, language), spoken)


def _pcm_slices(audio: bytes, step: int = 8192):
    for index in range(0, len(audio), step):
        yield audio[index : index + step]


async def _sarvam_pcm(text: str, language: str, speaker: str):
    import websockets

    spoken = _spoken_text(text)
    if not spoken:
        return
    uri = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true"
    async with websockets.connect(
        uri,
        additional_headers={"Api-Subscription-Key": _sarvam_key()},
        open_timeout=15,
        max_size=8_000_000,
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "config",
                    "data": {
                        "model": SARVAM_TTS_MODEL,
                        "language_code": language,
                        "speaker": speaker,
                        "speech_sample_rate": str(PCM_RATE),
                        "output_audio_codec": "linear16",
                        "min_buffer_size": 30,
                        "max_chunk_length": 80,
                    },
                }
            )
        )
        await socket.send(json.dumps({"type": "text", "data": {"text": spoken}}))
        await socket.send(json.dumps({"type": "flush"}))
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=40)
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "audio":
                chunk = base64.b64decode((message.get("data") or {}).get("audio") or "")
                if chunk:
                    yield chunk
            elif kind == "event" and (message.get("data") or {}).get("event_type") == "final":
                return
            elif kind == "error":
                detail = (message.get("data") or {}).get("message") or message
                raise RuntimeError(f"Sarvam stream failed: {detail}")


async def iter_pcm(text: str, language: str = "en-IN", speaker: str = ""):
    """Stream 16-bit PCM. Short lines are cached so a preview replays immediately."""
    key = _pcm_key(text, language, speaker)
    if not key[2]:
        return
    owner = False
    future: asyncio.Future[bytes] | None = None
    waiter: asyncio.Future[bytes] | None = None
    async with _pcm_gate:
        cached = _pcm_cache.get(key)
        if cached is None:
            waiter = _pcm_inflight.get(key)
            if waiter is None:
                future = asyncio.get_running_loop().create_future()
                _pcm_inflight[key] = future
                owner = True
    if cached is not None:
        for part in _pcm_slices(cached):
            yield part
        return
    if waiter is not None:
        audio = await waiter
        for part in _pcm_slices(audio):
            yield part
        return
    parts: list[bytes] = []
    try:
        async for chunk in _sarvam_pcm(key[2], key[1], key[0]):
            parts.append(chunk)
            yield chunk
        audio = b"".join(parts)
        if len(key[2]) <= 420:
            _pcm_cache[key] = audio
        if future is not None and not future.done():
            future.set_result(audio)
    except GeneratorExit:
        if future is not None and not future.done():
            future.set_result(b"".join(parts))
        raise
    except Exception as exc:
        if future is not None and not future.done():
            future.set_exception(exc)
        raise
    finally:
        async with _pcm_gate:
            _pcm_inflight.pop(key, None)


class SarvamStream:
    """One Bulbul session for a whole reply, so sentences are not separate requests."""

    def __init__(self, language: str, speaker: str):
        self.language_hint = language
        self.language = "en-IN"
        self.speaker = normalize_speaker(speaker)
        self.sent = ""
        self._socket = None

    async def open(self, text: str = "") -> None:
        self.language = _tts_language(text, self.language_hint)
        import websockets

        self._socket = await websockets.connect(
            "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true",
            additional_headers={"Api-Subscription-Key": _sarvam_key()},
            open_timeout=15,
            max_size=8_000_000,
        )
        await self._socket.send(
            json.dumps(
                {
                    "type": "config",
                    "data": {
                        "model": SARVAM_TTS_MODEL,
                        "language_code": self.language,
                        "speaker": self.speaker,
                        "speech_sample_rate": str(PCM_RATE),
                        "output_audio_codec": "linear16",
                        "min_buffer_size": 30,
                        "max_chunk_length": 80,
                    },
                }
            )
        )

    async def push_full(self, text: str) -> None:
        spoken = _spoken_text(text)
        if not spoken.startswith(self.sent):
            return
        delta = spoken[len(self.sent) :]
        if not delta.strip() or self._socket is None:
            return
        self.sent = spoken
        while delta:
            piece, delta = delta[:2400], delta[2400:]
            await self._socket.send(json.dumps({"type": "text", "data": {"text": piece}}))

    async def finish(self) -> None:
        if self._socket is not None and self.sent.strip():
            await self._socket.send(json.dumps({"type": "flush"}))

    async def iter_audio(self):
        if self._socket is None:
            return
        while True:
            try:
                raw = await asyncio.wait_for(self._socket.recv(), timeout=45)
            except Exception:
                return
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "audio":
                chunk = base64.b64decode((message.get("data") or {}).get("audio") or "")
                if chunk:
                    yield chunk
            elif kind == "event" and (message.get("data") or {}).get("event_type") == "final":
                return
            elif kind == "error":
                return

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None


async def warm_previews(language: str = "en-IN", speaker: str = "") -> None:
    code = language if language and language != "unknown" else "en-IN"
    names = list(PREVIEW_SPEAKERS)
    chosen = (speaker or "").strip().lower()
    names.sort(key=lambda item: 0 if item[0] == chosen else 1)
    for value, label in names:
        text = preview_line(code, label)
        try:
            async for _chunk in iter_pcm(text, code, value):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            continue


def trace_spoken_reply(
    text: str,
    thread_id: str = "",
    user_id: str = "",
) -> dict:
    """Keep the old logging helper for browser-only playback fallbacks."""
    spoken = _spoken_text(text)
    return {
        "text": spoken,
        "chars": len(spoken),
        "thread_id": thread_id,
        "user_id": user_id,
    }
