import os

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv()

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NEMOTRON_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

SYSTEM_PROMPT = ""


def _env(*names: str, default: str = "") -> str:
    """First non-empty env value, with quotes stripped."""
    for name in names:
        value = (os.getenv(name) or "").strip().strip("'").strip('"')
        if value:
            return value
    return default


def _clean(value: str, default: str = "") -> str:
    text = (value or "").strip().strip("'").strip('"')
    return text or default


def _api_key(value: str, default: str = "") -> SecretStr | None:
    text = _clean(value, default)
    return SecretStr(text) if text else None


#----------------------------------------------------------
# Gemma4 E2B
#----------------------------------------------------------
def load_gemma4_e2b(
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    max_tokens: int = 2048,
) -> BaseChatModel:
    # reasoning off: Gemma otherwise fills reasoning_content and leaves content empty.
    return ChatOpenAI(
        base_url=_clean(llm_base_url),
        api_key=_api_key(llm_api_key),
        model=_clean(llm_model),
        temperature=0.8,
        max_completion_tokens=max_tokens,
        disable_streaming=False,
        extra_body={"reasoning": "off"},
        reasoning_effort="low",
    )

def call_gemma4_e2b(llm, question: str, history: list | None = None) -> str:
    """Single call for agents. Stateless unless the caller passes history."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("history", optional=True),
        ("human", "{question}"),
    ])
    chain = prompt | llm
    if isinstance(llm, BaseChatModel):
        chain = chain | StrOutputParser()
    return chain.invoke({"question": question, "history": history or []})


#----------------------------------------------------------
# Gemini (OpenAI-compatible Google API)
#----------------------------------------------------------
class GeminiChatOpenAI(ChatOpenAI):
    """Gemini tool calls must be sent back with a thought signature.

    ChatOpenAI drops that field, so the follow-up turn after a tool call
    returns HTTP 400. The documented bypass is used when the real signature
    was not kept.
    """

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message in payload.get("messages") or []:
            for tool_call in message.get("tool_calls") or []:
                google = ((tool_call.get("extra_content") or {}).get("google") or {})
                if google.get("thought_signature"):
                    continue
                tool_call["extra_content"] = {
                    "google": {"thought_signature": "skip_thought_signature_validator"}
                }
        return payload


def load_gemini(
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    max_tokens: int = 2048,
) -> BaseChatModel:
    return GeminiChatOpenAI(
        base_url=_clean(llm_base_url, DEFAULT_GEMINI_BASE_URL),
        api_key=_api_key(llm_api_key, _env("GEMINI_API_KEY_3")),
        model=_clean(llm_model, _env("GEMINI_MODEL", default="gemini-3.8-flash")),
        max_completion_tokens=max_tokens,
        disable_streaming=False,
        reasoning_effort="low",
    )


def gemini_llm(max_tokens: int = 2048) -> BaseChatModel:
    """Reads Gemini settings from the environment."""
    return load_gemini(
        llm_base_url=_env("GEMINI_BASE_URL", default=DEFAULT_GEMINI_BASE_URL),
        llm_api_key=_env("GEMINI_API_KEY_3", "GEMINI_API_KEY"),
        llm_model=_env("GEMINI_MODEL", default="gemini-3.8-flash"),
        max_tokens=max_tokens,
    )


load_gemini_flash = load_gemini
gemini_flash_llm = gemini_llm


#----------------------------------------------------------
# NVIDIA Nemotron 3.5 Super VL
#----------------------------------------------------------
def _nemotron_model(name: str) -> str:
    text = _clean(name)
    if not text or "BF16" in text or "NVIDIA-Nemotron" in text:
        return DEFAULT_NEMOTRON_MODEL
    return text


def load_nemotron(
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    max_tokens: int = 2048,
) -> BaseChatModel:
    """OpenAI-compatible NVIDIA NIM client."""
    return ChatOpenAI(
        base_url=_clean(llm_base_url, _env("NVIDIA_BASE_URL", default=DEFAULT_NVIDIA_BASE_URL)),
        api_key=_api_key(llm_api_key, _env("NVIDIA_API_KEY")),
        model=_nemotron_model(llm_model or _env("NVIDIA_MODEL")),
        temperature=0.7,
        max_completion_tokens=max_tokens,
        disable_streaming=False,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def nemotron_llm(max_tokens: int = 2048) -> BaseChatModel:
    """Reads NVIDIA Nemotron settings from the environment."""
    return load_nemotron(
        llm_base_url=_env("NVIDIA_BASE_URL", default=DEFAULT_NVIDIA_BASE_URL),
        llm_api_key=_env("NVIDIA_API_KEY"),
        llm_model=_env("NVIDIA_MODEL", default=DEFAULT_NEMOTRON_MODEL),
        max_tokens=max_tokens,
    )


load_nemotron_3_5_super_vl = load_nemotron
nenotron_llm = nemotron_llm
