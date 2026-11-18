"""LangSmith tracing for LangChain runnables and the Python steps around them.

Turn it on with the variables in `.env`:
LANGSMITH_TRACING, LANGSMITH_ENDPOINT, LANGSMITH_API_KEY, LANGSMITH_PROJECT.
ChatOpenAI, retrievers, and other runnables are traced automatically once those
variables are loaded. `@traceable` covers Python functions that are not runnables.
"""
import os

from dotenv import load_dotenv

load_dotenv()

if os.getenv("LANGSMITH_TRACING", "").lower() in {"1", "true", "yes"}:
    os.environ["LANGSMITH_TRACING"] = "true"
    # LangChain still reads this name when it attaches the tracer to a runnable.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"

from langsmith import trace, traceable  # noqa: E402


def text_inputs(inputs: dict) -> dict:
    """Drop model objects so a trace records the prompt, not the weights."""
    recorded = {}
    for key, value in inputs.items():
        if key in {"self", "tokenizer", "model", "processor", "table"}:
            continue
        if isinstance(value, str):
            recorded[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            recorded[key] = value
        else:
            recorded[key] = type(value).__name__
    return recorded


def voice_inputs(inputs: dict) -> dict:
    """Log recording metadata, not the raw audio bytes."""
    audio = inputs.get("audio")
    size = len(audio) if isinstance(audio, (bytes, bytearray)) else 0
    return {
        "bytes": size,
        "mime": inputs.get("mime") or inputs.get("content_type"),
        "filename": inputs.get("filename"),
        "thread_id": inputs.get("thread_id"),
        "user_id": inputs.get("user_id"),
        "language": inputs.get("language"),
        "text_preview": str(inputs.get("text") or "")[:240],
    }


def voice_outputs(result):
    if isinstance(result, dict):
        transcript = result.get("transcript") or result.get("text") or ""
        return {
            "transcript": str(transcript)[:500],
            "chars": len(str(transcript)),
            "model": result.get("model"),
            "language": result.get("language"),
            "provider": result.get("provider"),
            "audio_bytes": len(result["audio"]) if isinstance(result.get("audio"), (bytes, bytearray)) else result.get("bytes"),
        }
    return {"result": type(result).__name__}


__all__ = [
    "text_inputs",
    "trace",
    "traceable",
    "voice_inputs",
    "voice_outputs",
]
