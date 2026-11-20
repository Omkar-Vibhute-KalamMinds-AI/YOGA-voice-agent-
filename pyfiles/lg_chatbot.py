import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
import dotenv
import openai
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage, AIMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from typing import Any, Literal, TypedDict, Annotated
from pydantic import BaseModel, Field
from ModelLoader import load_gemini, load_gemma4_e2b, nemotron_llm
from langgraph.config import get_stream_writer
from langgraph.graph import add_messages
from langgraph.types import Command, interrupt
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres import PostgresStore
from langchain_core.runnables import RunnableConfig
from sys import base_prefix
from langsmith import traceable

#---------------------------------State---------------------------------
class ChatState(TypedDict):   
    messages: Annotated[list[BaseMessage], add_messages]
     #all type of messages are inhereted from BaseMessage 

dotenv.load_dotenv()

#---------------------------------LLM_gemini_flash---------------------------------
# Built only when a key is present, so a hosted deploy can start on NVIDIA alone.
llm_gemini_flash = (
    load_gemini(
        llm_base_url=os.getenv("GEMINI_BASE_URL") or "",
        llm_api_key=os.getenv("GEMINI_API_KEY_3") or "",
        llm_model=os.getenv("GEMINI_MODEL") or "",
        max_tokens=1024,
    )
    if (os.getenv("GEMINI_API_KEY_3") or os.getenv("GEMINI_API_KEY"))
    else None
)



#---------------------------------LLM_nemotron---------------------------------
llm_nemotron = nemotron_llm(max_tokens=4096)
llm_guard = nemotron_llm(max_tokens=24)

#---------------------------------Tools---------------------------------
from chatbot_tools import tools as local_tools
from client import load_tools
from langgraph.checkpoint.memory import MemorySaver
llm_with_tools = None
tools_by_name: dict[str, Any] = {}

from langchain_core.runnables import Runnable
from langchain_core.messages import BaseMessage

llm_with_tools: Runnable[list[BaseMessage], BaseMessage] | None = None

checkpointer = MemorySaver()
chatbot = None
store: BaseStore | None = None

#---------------------------------User ID---------------------------------
# Name stored in config["configurable"]["user_id"]. The memory namespace is
# ("Users", user_id, "memory").
USER_ID = "Fardeen"

SEED_MEMORIES = (
    ("info1u1", "Omkar is IIT Madras's drop out student, building in PeacockRides; an Agentic Ride booking system."),
    ("info2u1", "Omkar is from Nanded, Maharashtra, India. And leaving in Taramani, Chennai, Tamil Nadu, India."),
    ("info3u1", "Omkar's Agentic Ride booking system is called 'PeacockRides'"),
    ("info4u1", "Omkar is a great fan of A.P.J. Abdul Kalam"),
    ("info5u1", "Omkar's Agentic Ride booking system is called 'PeacockRides'"),
    ("info6u1", "Omkar will is loves of book reading"),
    ("info7u1", 'Omkar will be launching PeacockRides by mid of 2027"'),
)

#---------------------------------System Prompt---------------------------------
from system_prompt import SYSTEM_PROMPT_TEMPLATE
from guard import (
    INPUT_CHECK_SYSTEM,
    OUTPUT_CHECK_PROMPT,
    REFUSAL,
    SAFE_REPLY,
    OutputGuard,
    blocks_verdict,
    isolate,
    prompt_has_secret,
    screen_input,
)
from system_prompt import MEMORY_CREATOR_PROMPT

class MemoryWrite(BaseModel):
    """Facts worth keeping for later conversations."""
    should_write: bool = False
    memories: list[str] = Field(default_factory=list)

def normalize_user_id(user_id: str | None) -> str:
    """Same person whether the name is Fardeen, fardeen, or FARDEEN."""
    text = (user_id or "").strip()
    return (text or USER_ID).casefold()

def seed_user_ids() -> set[str]:
    """Default memory user, plus the name used in the seed sentences."""
    ids = {normalize_user_id(USER_ID)}
    if SEED_MEMORIES:
        first_name = SEED_MEMORIES[0][1].split()[0].strip(" ,.'\"")
        if first_name:
            ids.add(normalize_user_id(first_name))
    return ids

def memory_namespace(config: RunnableConfig) -> tuple[str, str, str]:
    """("Users", <user id from config>, "memory")."""
    configurable = config.get("configurable") or {}
    user_id = normalize_user_id(str(configurable.get("user_id") or USER_ID))
    return ("Users", user_id, "memory")

def latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message_text(message.content).strip()
    return ""

def _fact_line(value: object) -> str:
    data = value.get("data") if isinstance(value, dict) else None
    return data.strip() if isinstance(data, str) else ""


def load_user_memory(memory_store: BaseStore, namespace: tuple[str, str, str], query: str) -> str:
    """Long-term facts for this turn, with seed facts first for the default user."""
    lines: list[str] = []
    seen: set[str] = set()

    def add_fact(text: str) -> None:
        fact = text.strip()
        key = fact.casefold()
        if not fact or key in seen:
            return
        seen.add(key)
        lines.append(f"- {fact}")

    if namespace[1] in seed_user_ids():
        for key, fact in SEED_MEMORIES:
            item = memory_store.get(namespace, key)
            stored = _fact_line(item.value) if item is not None else ""
            add_fact(stored or fact)
    for item in memory_store.search(namespace, limit=50):
        add_fact(_fact_line(item.value if item is not None else None))
    return "\n".join(lines) if lines else "(none)"

def seed_user_memory(memory_store: BaseStore) -> None:
    """Always write seed keys so updated facts replace stale ones."""
    for user in seed_user_ids():
        namespace = ("Users", user, "memory")
        for key, fact in SEED_MEMORIES:
            memory_store.put(namespace, key, {"data": fact})
        print(f"Seed memory ready for user {user} ({len(SEED_MEMORIES)} facts).")

@contextmanager
def open_memory_store() -> Iterator[BaseStore]:
    """Use Postgres when it is up. If that fails, keep running on InMemoryStore."""
    uri = (os.getenv("POSTGRES_URI") or "").strip()
    connect_uri = ""
    if uri:
        connect_uri = uri if "connect_timeout=" in uri else (
            uri + ("&" if "?" in uri else "?") + "connect_timeout=5"
        )
    if connect_uri:
        postgres_cm = PostgresStore.from_conn_string(connect_uri)
        try:
            memory_store = postgres_cm.__enter__()
            memory_store.setup()
            seed_user_memory(memory_store)
        except Exception as exc:
            postgres_cm.__exit__(type(exc), exc, exc.__traceback__)
            print(f"Postgres is not reachable ({exc}). Using InMemoryStore for this run.")
        else:
            try:
                print("Long-term memory: Postgres")
                yield memory_store
            finally:
                postgres_cm.__exit__(None, None, None)
            return
    else:
        print("POSTGRES_URI is not set. Using InMemoryStore for this run.")
    memory_store = InMemoryStore()
    seed_user_memory(memory_store)
    yield memory_store 

#---------------------------------Chat Node---------------------------------

def _reply_language_instruction(code: str) -> str:
    labels = {
        "hi-IN": "Hindi, written in Devanagari. Do not answer in English.",
        "mr-IN": "Marathi, written in Devanagari. Do not answer in English.",
        "te-IN": "Telugu, written in Telugu script. Do not answer in English.",
        "kn-IN": "Kannada, written in Kannada script. Do not answer in English.",
        "ml-IN": "Malayalam, written in Malayalam script. Do not answer in English.",
        "ta-IN": "Tamil, written in Tamil script. Do not answer in English.",
        "pa-IN": "Punjabi, written in Gurmukhi. Do not answer in English.",
        "od-IN": "Odia, written in Odia script. Do not answer in English.",
        "bn-IN": "Bengali, written in Bengali script. Do not answer in English.",
        "gu-IN": "Gujarati, written in Gujarati script. Do not answer in English.",
        "en-IN": "English",
        "en-US": "English",
    }
    text = (code or "").strip()
    if text in labels:
        return labels[text]
    return (
        "the same language and native script the user just used. "
        "If they spoke Marathi, Hindi, Tamil, Telugu, or any Indian language, "
        "answer in that language, not English."
    )


def _checker_says_block(messages: list[BaseMessage]) -> bool:
    """One short call to the guard model. A failure leaves the regex checks in charge."""
    try:
        result = llm_guard.invoke(messages)
    except Exception as exc:
        print(f"Guard model skipped: {exc}")
        return False
    return blocks_verdict(message_text(getattr(result, "content", result)))


def llm_input_refusal(text: str) -> str | None:
    """Separate model call on the user message, before the main model."""
    if not (text or "").strip():
        return None
    assistant_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        user_memory="",
        reply_language="the language the user just used",
    )
    system = INPUT_CHECK_SYSTEM + "\n\nAssistant instructions you are protecting:\n" + assistant_prompt
    if _checker_says_block([
        SystemMessage(content=system),
        HumanMessage(content=text),
    ]):
        return REFUSAL
    return None


def release_reply(text: str) -> str:
    """Separate model call on the main model's reply, then the local output scan."""
    spoken = (text or "").strip()
    if not spoken:
        return ""
    if _checker_says_block([
        HumanMessage(content=OUTPUT_CHECK_PROMPT.format(text=spoken.replace("{", "{{").replace("}", "}}"))),
    ]):
        return SAFE_REPLY
    output = OutputGuard()
    released = output.feed(spoken) + output.finish()
    return released or spoken


@traceable(name="chat_node", run_type="chain", tags=["chatbot"])
def chat_node(state: ChatState, config: RunnableConfig, store: BaseStore) -> ChatState:
    """LangGraph node. Sends thread history plus this user's long-term memory."""
    global llm_with_tools
    user_memory = load_user_memory(store, memory_namespace(config), latest_user_text(state["messages"]))
    reply_language = _reply_language_instruction(
        str((config.get("configurable") or {}).get("reply_language") or "")
    )
    prompt = SYSTEM_PROMPT_TEMPLATE.format(user_memory=user_memory, reply_language=reply_language)
    if prompt_has_secret(prompt):
        prompt = SYSTEM_PROMPT_TEMPLATE.format(user_memory="", reply_language=reply_language)
    message: list[BaseMessage] = [
        SystemMessage(content=prompt),
        *state["messages"],
    ]

    if llm_with_tools is None:
        raise ValueError("LLM with tools is not initialized")

    writer = get_stream_writer()
    accumulated: Any = None
    for chunk in llm_with_tools.stream(message):
        accumulated = chunk if accumulated is None else accumulated + chunk
    if accumulated is None:
        raise ValueError("The model returned no reply")
    spoken = release_reply(message_text(accumulated.content))
    if spoken:
        writer({"token": spoken})
    tool_calls = [] if spoken == SAFE_REPLY else list(getattr(accumulated, "tool_calls", []) or [])
    response = AIMessage(
        content=spoken,
        tool_calls=tool_calls,
        additional_kwargs=dict(getattr(accumulated, "additional_kwargs", {}) or {}),
        id=getattr(accumulated, "id", None),
    )
    return {"messages": [response]}

#---------------------------------Memory Creator---------------------------------
def _write_user_memory(state: dict[str, Any], config: RunnableConfig, store: BaseStore) -> dict:
    """Write stable user facts from this turn into the long-term store."""
    messages = list(state.get("messages") or [])
    user_text = latest_user_text(messages)
    assistant_text = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            assistant_text = message_text(message.content).strip()
            if assistant_text:
                break
    if not user_text:
        return {}
    try:
        extractor = llm_nemotron.with_structured_output(MemoryWrite, method="function_calling")
        result = extractor.invoke([
            SystemMessage(content=MEMORY_CREATOR_PROMPT),
            HumanMessage(content=f"User: {user_text}\nAssistant: {assistant_text}"),
        ])
    except Exception as exc:
        print(f"\nMemory not saved: {exc}")
        return {}

    if isinstance(result, MemoryWrite):
        should_write = result.should_write
        memories = result.memories
    elif isinstance(result, dict):
        should_write = bool(result.get("should_write"))
        memories = list(result.get("memories") or [])
    else:
        return {}
    if not should_write:
        return {}

    namespace = memory_namespace(config)
    known = {
        str(item.value.get("data", "")).strip().lower()
        for item in store.search(namespace, limit=50)
        if isinstance(item.value, dict)
    }
    saved: list[str] = []
    for fact in memories:
        text = str(fact).strip()
        if not text or text.lower() in known:
            continue
        store.put(namespace, str(uuid.uuid4()), {"data": text})
        known.add(text.lower())
        saved.append(text)
    if saved:
        print("\nMemory saved: " + "; ".join(saved))
    return {}


async def _save_memory_later(config: RunnableConfig) -> None:
    """Write long-term facts after the spoken reply has already started."""
    if chatbot is None or store is None:
        return
    try:
        snapshot = chatbot.get_state(config)
        values = dict(snapshot.values or {})
        if "messages" not in values:
            values["messages"] = []
        await asyncio.to_thread(_write_user_memory, values, config, store)
    except Exception as exc:
        print(f"Memory not saved: {exc}")

#---------------------------------Human approval---------------------------------
from datetime import date, datetime

EXPENSE_FIELDS = ("date", "amount", "category", "subcategory", "note")


def approval_kind(tool_name: str) -> str | None:
    """Expense adds and Wikipedia searches wait for a person. Other tools run immediately."""
    lowered = tool_name.replace("-", "_").lower()
    if "wiki" in lowered:
        return "wikipedia"
    add_words = ("add", "create", "insert", "log", "save", "record")
    if "expense" in lowered and any(word in lowered for word in add_words):
        return "expense_add"
    return None


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return date.today().isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def complete_expense_args(args: dict[str, Any]) -> dict[str, Any]:
    """Keep the MCP add/edit payload, and fill date when the model skipped it."""
    lowered = {str(key).lower(): value for key, value in args.items()}
    filled: dict[str, Any] = {}
    if "id" in lowered:
        filled["id"] = lowered["id"]
    filled["date"] = _iso_date(lowered.get("date") or lowered.get("expense_date"))
    filled["amount"] = lowered.get("amount") if lowered.get("amount") not in (None, "") else ""
    filled["category"] = lowered.get("category") or ""
    filled["subcategory"] = lowered.get("subcategory") or ""
    filled["note"] = lowered.get("note") or lowered.get("description") or ""
    return filled


def show_record(args: dict[str, Any]) -> str:
    return json.dumps(args, indent=2, ensure_ascii=False)


def spoken_review_question(kind: str, args: dict[str, Any]) -> str:
    if kind == "wikipedia":
        query = str(args.get("query") or "").strip() or "this topic"
        return f"To answer you, I need to search Wikipedia for {query}. May I?"
    amount = args.get("amount")
    category = str(args.get("category") or "an expense").strip() or "an expense"
    day = args.get("date") or date.today().isoformat()
    note = str(args.get("note") or "").strip()
    spoken = f"Should I add {amount} for {category} on {day} to your expense tracker?"
    if note:
        spoken += f" Note: {note}."
    return spoken + " Say yes, no, or edit."


def review_request(call: dict[str, Any]) -> dict[str, Any]:
    args = dict(call.get("args") or {})
    kind = approval_kind(str(call.get("name") or "")) or ""
    if kind == "expense_add":
        args = complete_expense_args(args)
    record = show_record(args)
    return {
        "id": call.get("id"),
        "name": call.get("name"),
        "kind": kind,
        "args": args,
        "record": record,
        "question": spoken_review_question(kind, args),
    }

def decision_map(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("decisions"), list):
        raw = raw["decisions"]
    if isinstance(raw, list):
        return {
            str(item.get("id")): item
            for item in raw
            if isinstance(item, dict) and item.get("id")
        }
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    return {}

def edited_args(record: str, args: dict[str, Any], kind: str) -> dict[str, Any]:
    text = record.strip()
    if not text:
        return args
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    if kind == "wikipedia":
        return {**args, "query": text}
    return {**args, "note": text}

async def invoke_tool(call: dict[str, Any], args: dict[str, Any]) -> ToolMessage:
    tool = tools_by_name.get(str(call.get("name")))
    if tool is None:
        return ToolMessage(
            content=f"Tool {call.get('name')} is not available.",
            tool_call_id=str(call.get("id")),
            name=str(call.get("name")),
            status="error",
        )
    payload = {"name": call["name"], "args": args, "id": call["id"], "type": "tool_call"}
    if getattr(tool, "coroutine", None):
        result = await tool.ainvoke(payload)
    else:
        result = await asyncio.to_thread(tool.invoke, payload)
    if isinstance(result, ToolMessage):
        tag = "untrusted_document" if "wiki" in str(call.get("name") or "").lower() else "untrusted_tool_output"
        result.content = isolate(message_text(result.content), tag)
        return result
    if isinstance(result, tuple):
        result = result[0]
    content = result if isinstance(result, str) else json.dumps(result, default=str)
    tag = "untrusted_document" if "wiki" in str(call.get("name") or "").lower() else "untrusted_tool_output"
    return ToolMessage(content=isolate(content, tag), tool_call_id=str(call["id"]), name=str(call["name"]))

async def execute_tools(state: ChatState) -> dict:
    """Run tools. Expense adds and Wikipedia searches pause until the person answers."""
    last = state["messages"][-1]
    calls = [dict(call) for call in (getattr(last, "tool_calls", None) or [])]
    gated = [call for call in calls if approval_kind(str(call.get("name") or ""))]
    decisions: dict[str, dict[str, Any]] = {}
    if gated:
        decisions = decision_map(interrupt({"requests": [review_request(call) for call in gated]}))
    messages: list[ToolMessage] = []
    for call in calls:
        kind = approval_kind(str(call.get("name") or ""))
        args = dict(call.get("args") or {})
        if kind == "expense_add":
            args = complete_expense_args(args)
        if kind:
            decision = decisions.get(str(call.get("id")), {})
            action = str(decision.get("action", "no")).strip().lower()
            record = show_record(args)
            if action in {"no", "n"}:
                if kind == "wikipedia":
                    content = f"Wikipedia search was not approved for query: {args.get('query', record)}"
                else:
                    content = f"record {record} is not added"
                messages.append(ToolMessage(content=content, tool_call_id=str(call.get("id")), name=str(call.get("name"))))
                continue
            if action == "edit":
                args = edited_args(str(decision.get("record") or ""), args, kind)
                if kind == "expense_add":
                    args = complete_expense_args(args)
        messages.append(await invoke_tool(call, args))
    return {"messages": messages}

#---------------------------------Check Tool Requirement---------------------------------
def check_tool_requirement(state: ChatState) -> Literal["has_tool_use", "no_tool_use"]:
    """Route to the tool node when the latest model message requests a tool."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "has_tool_use"
    return "no_tool_use"

#---------------------------------Graph---------------------------------
async def setup() -> None:
    """Load MCP tools on the chat loop, then compile the graph."""
    global llm_with_tools, chatbot, tools_by_name
    if store is None:
        raise RuntimeError("Long-term memory store is not open")
    try:
        remote_tools = await load_tools()
    except Exception as exc:
        print(f"Expense tools skipped: {exc}")
        remote_tools = []
    tools = [*local_tools, *remote_tools]
    llm_with_tools = llm_nemotron.bind_tools(tools)
    tools_by_name = {tool.name: tool for tool in tools}

    graph = StateGraph(ChatState)
    graph.add_node("chat", chat_node)
    graph.add_node("tool", execute_tools)
    graph.add_edge(START, "chat")
    graph.add_conditional_edges("chat", check_tool_requirement,
             {"has_tool_use": "tool", "no_tool_use": END})
    graph.add_edge("tool", "chat")
    chatbot = graph.compile(checkpointer=checkpointer, store=store)

def message_text(content: object) -> str:
    """Plain text from a string reply or a list of content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)

#---------------------------------Chat Turn---------------------------------
def _stream_part(part: object) -> tuple[str | None, object, object]:
    """Normalize a single-mode chunk or a (mode, data) pair from astream."""
    if isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], str):
        return part[0], part[1], ()
    return getattr(part, "type", None), getattr(part, "data", part), getattr(part, "interrupts", ()) or ()

def _interrupt_requests(pending: object) -> list[dict[str, Any]]:
    items = pending if isinstance(pending, (list, tuple)) else [pending]
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and isinstance(value.get("requests"), list):
            return value["requests"]
        if isinstance(item, dict):
            nested = item.get("value")
            if isinstance(nested, dict) and isinstance(nested.get("requests"), list):
                return nested["requests"]
    return []

def cli_decisions(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ask yes, no, or edit in the terminal before a gated tool runs."""
    answers: list[dict[str, Any]] = []
    for request in requests:
        print()
        print(request.get("question", "May I continue?"))
        print(request.get("record", ""))
        choice = input("yes / no / edit: ").strip().lower()
        if choice in {"edit", "e"}:
            edited = input("Edited record: ").strip()
            answers.append({"id": request.get("id"), "action": "edit", "record": edited})
        elif choice in {"yes", "y"}:
            answers.append({"id": request.get("id"), "action": "yes"})
        else:
            answers.append({"id": request.get("id"), "action": "no"})
    print("AI: ", end="", flush=True)
    return answers

class StreamText(TypedDict):
    type: Literal["text"]
    text: str

class StreamReplyDone(TypedDict):
    type: Literal["reply_done"]

class StreamInterrupt(TypedDict):
    type: Literal["interrupt"]
    requests: list[dict[str, Any]]


def _chat_reply_ready(data: object) -> bool:
    if not isinstance(data, dict) or "chat" not in data:
        return False
    payload = data["chat"]
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not messages:
        return False
    last = messages[-1]
    return not (getattr(last, "tool_calls", None) or [])

async def stream_reply(
    thread_id: str,
    user_message: str,
    user_id: str | None = None,
    resume: Any = None,
    language: str = "",
) -> AsyncIterator[StreamText | StreamReplyDone | StreamInterrupt]:
    """Yield text tokens, then an interrupt event when a tool needs approval."""
    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": normalize_user_id(user_id),
            "reply_language": language or "the language the user just used",
        }
    }
    if chatbot is None:
        raise ValueError("Chatbot is not initialized")
    if resume is None:
        refusal = screen_input(user_message) or llm_input_refusal(user_message)
        if refusal:
            yield {"type": "text", "text": refusal}
            yield {"type": "reply_done"}
            return
    graph_input: Any = (
        Command(resume=resume)
        if resume is not None
        else {"messages": [HumanMessage(content=user_message)]}
    )
    pending: object = ()
    replied = False
    async for part in chatbot.astream(
        graph_input,
        config=config,
        stream_mode=["custom", "updates"],
    ):
        mode, data, interrupts = _stream_part(part)
        if interrupts:
            pending = interrupts
        if mode == "custom" and isinstance(data, dict):
            token = data.get("token")
            if isinstance(token, str) and token:
                yield {"type": "text", "text": token}
        if mode == "updates" and isinstance(data, dict):
            if data.get("__interrupt__"):
                pending = data["__interrupt__"]
            elif _chat_reply_ready(data):
                replied = True
                yield {"type": "reply_done"}
    if not _interrupt_requests(pending):
        snapshot = chatbot.get_state(config)
        pending = getattr(snapshot, "interrupts", ()) or ()
    requests = _interrupt_requests(pending)
    if requests:
        yield {"type": "interrupt", "requests": requests}
        return
    if not replied:
        yield {"type": "reply_done"}
    asyncio.create_task(_save_memory_later(config))

def _api_error_text(exc: openai.APIError) -> str:
    detail = getattr(exc, "body", None) or exc
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        detail = (detail[0].get("error") or {}).get("message", detail)
    elif isinstance(detail, dict):
        detail = (detail.get("error") or {}).get("message", detail)
    return str(detail)

@traceable(name="chat_turn", run_type="chain", tags=["chatbot"])
async def chat_turn(thread_id: str, user_message: str) -> str:
    """One user turn. The return value is markdown shown on the LangSmith trace."""
    parts: list[str] = []
    print("AI: ", end="", flush=True)
    resume: Any = None
    try:
        while True:
            interrupted = False
            async for event in stream_reply(thread_id, user_message, resume=resume):
                if event["type"] == "text":
                    print(event["text"], end="", flush=True)
                    parts.append(event["text"])
                elif event["type"] == "interrupt":
                    resume = cli_decisions(event["requests"])
                    interrupted = True
                    break
            if not interrupted:
                break
    except openai.APIError as exc:
        print(f"\nRequest failed: {_api_error_text(exc)}")
        answer = ""
    else:
        print()
        answer = "".join(parts)
    return f"**User:** {user_message}\n\n**Assistant:** {answer}"

#---------------------------------Thread Config---------------------------------
def thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}

def rewind_before_user_turn(thread_id: str, user_index: int) -> None:
    """Drop that user message and every later message so a new turn can replace it."""
    if chatbot is None:
        raise ValueError("Chatbot is not initialized")
    if user_index < 0:
        raise ValueError("user_index must be 0 or greater")
    state = chatbot.get_state(thread_config(thread_id))
    messages = list((state.values or {}).get("messages", []))
    seen = -1
    cut = None
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and message_text(message.content).strip():
            seen += 1
            if seen == user_index:
                cut = index
                break
    if cut is None:
        raise ValueError("That user message is not in this thread")
    chatbot.update_state(
        thread_config(thread_id),
        {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages[:cut]]},
    )

#---------------------------------Saved Threads---------------------------------
def saved_threads() -> list[str]:
    """Thread ids stored by the in-memory checkpointer for this process."""
    return list(checkpointer.storage.keys())

#---------------------------------Show History---------------------------------
def thread_history(thread_id: str) -> list[dict[str, Any]]:
    """Saved messages for one thread, skipping empty tool-call placeholders."""
    if chatbot is None:
        raise ValueError("Chatbot is not initialized")
    state = chatbot.get_state(thread_config(thread_id))
    messages = (state.values or {}).get("messages", [])
    roles = {"human": "User", "ai": "AI", "tool": "Tool"}
    rows: list[dict[str, str]] = []
    user_turns = 0
    for message in messages:
        text = message_text(message.content).strip()
        if not text:
            continue
        role = roles.get(message.type, message.type)
        if role == "Tool":
            continue
        row: dict[str, Any] = {"role": role, "content": text}
        if role == "User":
            row["index"] = user_turns
            user_turns += 1
        rows.append(row)
    return rows

def show_history(thread_id: str) -> None:
    messages = thread_history(thread_id)
    if not messages:
        print("No messages in this thread yet.")
        return
    print(f"--- thread {thread_id} ---")
    for message in messages:
        print(f"{message['role']}: {message['content']}")
    print("---") 

#---------------------------------Call Model---------------------------------
@traceable(name="call_model", run_type="chain", tags=["chatbot"])
async def call_model(thread_id: str) -> str:
    """Chat loop for one thread. exit or bye returns to the menu. quit ends the program."""
    while True:
        user_message = (await asyncio.to_thread(
            input, "Type here (exit/bye = menu, quit = stop): "
        )).strip()
        print("User:", user_message)
        if user_message.lower() in ["exit", "bye", "quit"]:
            return user_message.lower()
        if not user_message:
            print("Type a message.")
            continue
        await chat_turn(thread_id, user_message)

#---------------------------------Main---------------------------------
async def main() -> None:
    global store
    with open_memory_store() as memory_store:
        store = memory_store
        await run_menu()

async def run_menu() -> None:
    await setup()
    while True:
        print("\n1. New chat")
        print("2. Resume chat")
        print("3. Quit")
        choice = (await asyncio.to_thread(input, "Choose: ")).strip()

        if choice == "1":
            thread_id = str(uuid.uuid4())
            print("New thread:", thread_id)
            if await call_model(thread_id) == "quit":
                break
        elif choice == "2":
            threads = saved_threads()
            if not threads:
                print("No chats yet. Start a new chat first.")
                continue
            for index, thread_id in enumerate(threads, start=1):
                print(f"{index}. {thread_id}")
            pick = (await asyncio.to_thread(input, "Thread number: ")).strip()
            if not pick.isdigit() or not 1 <= int(pick) <= len(threads):
                print("Invalid thread number.")
                continue
            thread_id = threads[int(pick) - 1]
            show_history(thread_id)
            if await call_model(thread_id) == "quit":
                break
        elif choice == "3":
            break
        else:
            print("Choose 1, 2, or 3.")

if __name__ == "__main__":
    asyncio.run(main()) 

