
Models and how they load

The chat model is a remote OpenAI-compatible endpoint, built in `ModelLoader.py` and constructed at import time in `lg_chatbot.py`. The live chat model is **NVIDIA Nemotron**. Wikipedia and age tools are local. Expense tools come from the MCP server. Voice STT/TTS is documented separately (Saaras / Bulbul).

It is a multi-turn agent.

How a turn runs:

- New chat (new UUID thread), resume a saved thread, or quit.
- `chat_turn` / `stream_reply` streams the user message through the compiled graph.
- The chat node sends the YOGA system prompt, long-term memory, and thread history to Nemotron.
- If the reply contains tool calls, the graph goes to the tool node, then back to chat. Expense **add** and Wikipedia search wait for human Yes / No / Edit. Otherwise it goes to the memory node and ends.
- `MemorySaver` keeps **thread history** for the life of the process, keyed by `thread_id`.

---

# Agent long-term memory

Long-term memory is **not** the chat transcript. It is stable facts about a person (name, job, city, preferences) that survive across threads.

## Two stores

| Store | Role |
|-------|------|
| `PostgresStore` (`langgraph.store.postgres`) | Primary. Facts live in Postgres. |
| `InMemoryStore` | Fallback if `POSTGRES_URI` is missing or Postgres is down. RAM only; lost when the process exits. |

`open_memory_store()` in `lg_chatbot.py` tries Postgres first (`setup()` creates tables), seeds facts if the namespace is empty, and falls back to InMemoryStore on connection timeout.

Thread **checkpoints** use `MemorySaver` (in-process). That is short-term conversation state, separate from the long-term fact store.

## Namespace and user id

Facts are keyed as `("Users", <user_id>, "memory")`.

- Sidebar **Memory user** maps to `config["configurable"]["user_id"]`.
- `normalize_user_id()` lowercases the name so `Fardeen` and `fardeen` share a namespace.
- Default user id is `Fardeen`.

Each fact is stored as `{ "data": "<one sentence>" }`.

## Read path (every chat turn)

`chat_node` calls `load_user_memory()`:

- It lists up to 20 stored facts for that user.
- Those lines are injected into the system prompt as `{user_memory}`.

## Write path (end of a turn with no pending tools)

`memory_creator` uses Nemotron with structured output `MemoryWrite` (`should_write`, `memories`). Prompt: only stable, user-specific facts; skip transient details. Duplicates (case-insensitive) are not written again.

## Environment and packages

```
POSTGRES_URI=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

From `pyproject.toml`:

- `langgraph-checkpoint-postgres>=3.2.0`
- `psycopg[binary,pool]>=3.2.0`

Example local port in this project: `5440`.

Seed facts (`SEED_MEMORIES`) load only when the Fardeen namespace is empty.

---

# MCP server (expense manager)

Expense CRUD is not implemented in this repo. The agent calls a **remote FastMCP** server over **Streamable HTTP**.

## Connection

| Item | Value |
|------|--------|
| Config | `servers.json` → `mcpServers.expense-manager` |
| URL | `https://remote-expense-manger-server.fastmcp.app/mcp` |
| Transport | `http` (Streamable HTTP). Do not use SSE. |
| Auth env | `EXPENSE_MCP_TOKEN` (token only, no `Bearer ` prefix) |
| Client | `pyfiles/client.py` → `load_tools()` |
| Bind time | `setup()` on the same async loop as chat |

Header: `Authorization: Bearer <EXPENSE_MCP_TOKEN>`.

Tool names are prefixed with the server key, e.g. `expense-manager_add_expense`.

## Tools (from `list_tools`)

| Public name | Remote name | Notes |
|-------------|-------------|--------|
| `expense-manager_add_expense` | `add_expense` | HITL before run. Required: `date` (YYYY-MM-DD), `amount`, `category`. Optional: `subcategory`, `note`. |
| `expense-manager_list_expenses` | `list_expenses` | Filters: `start_date`, `end_date`, `category`, `subcategory`, `limit`. No HITL. |
| `expense-manager_get_expense` | `get_expense` | Required: `id`. No HITL. |
| `expense-manager_edit_expense` | `edit_expense` | Required: `id`, `date`, `amount`, `category`. |
| `expense-manager_delete_expense` | `delete_expense` | Required: `id`. |

If the model omits **date** on add, the host fills **today** (`YYYY-MM-DD`) before HITL and before the MCP call.

## Human in the loop

`execute_tools` in `lg_chatbot.py`:

- **Pause:** expense **add**, Wikipedia search (`wikipedia_search` in `chatbot_tools.py`).
- **Run immediately:** list / get / age calculator (and other non-gated tools).
- UI: Yes / No / Edit fields (date picker, amount, category, …). The permission question is spoken.

Local (non-MCP) tools in `chatbot_tools.py`: `wikipedia_search`, `age_calculator`.

## Environment and packages

```
EXPENSE_MCP_TOKEN=
```

From `pyproject.toml`: `langchain[mcp]`, `fastmcp>=4.1.4`, `langchain-mcp-adapters>=0.3.1`. Client uses `mcp` `ClientSession` + `streamable_http_client` and `httpx`.

To add another MCP server, add a key under `mcpServers` in `servers.json` and the matching token in `.env`. `load_tools()` discovers every server at startup.


# Voice 

## Voice Agent

YOGA is a **voice-first LangGraph agent**. The browser records audio, the server transcribes it, Nemotron answers, then the server synthesizes speech and the browser plays it. Expense add and Wikipedia still pause for Yes / No / Edit. There is no extra confirm step on the transcript.

### Pipeline

1. Mic records in the browser (`MediaRecorder`), converted to 16 kHz mono WAV.
2. **Send** stops recording and posts the clip. The orange **X** cancels without sending.
3. `POST /api/voice/transcribe` → **Saaras v4** (speech-to-text).
4. Transcript is sent as a normal chat turn (`POST /api/chat`) to **NVIDIA Nemotron**.
5. `POST /api/voice/speak` → **Bulbul v3** (text-to-speech) → WAV playback.
6. HITL permission prompts are also spoken.
7. LangSmith traces `voice_transcribe` and `voice_speak` (metadata only, not raw audio).

Code: `pyfiles/voice.py`, routes in `pyfiles/app.py`, UI in `web/app.js`.

### Models (current defaults)

| Role | Provider | Model ID | API |
|------|----------|----------|-----|
| Speech-to-text | Sarvam | `saaras:v4` | `POST https://api.sarvam.ai/speech-to-text` |
| Text-to-speech | Sarvam | `bulbul:v3` | `POST https://api.sarvam.ai/text-to-speech` |
| Chat / tools | NVIDIA | `nvidia/nemotron-3.5-super-vl-120b-a12b` | `https://integrate.api.nvidia.com/v1` |

**Saaras v4:** REST STT, `mode=transcribe`. Auto language detect with `language_code=unknown`. Clips should stay under ~30 seconds (Sarvam REST limit). Header: `api-subscription-key`.

**Bulbul v3:** REST TTS, JSON in, base64 WAV out. Speaks Hindi, Marathi, Telugu, Kannada, Malayalam, Tamil, Punjabi, Odia, Bengali, Gujarati, and Indian English. Header: `api-subscription-key`.

One **Sarvam API subscription key** covers both Saaras and Bulbul. You do **not** need a Sarvam LLM / “voice agent product” key. Chat stays on NVIDIA.

Key: [dashboard.sarvam.ai](https://dashboard.sarvam.ai) → API Keys. Docs: [STT](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/rest-api), [TTS](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/rest-api).

Fallback (if `SARVAM_API_KEY` is empty, or `VOICE_*_PROVIDER=nvidia`):

| Role | NVIDIA model |
|------|----------------|
| STT | Parakeet CTC 1.1B ASR (English `en-US`) |
| TTS | Magpie TTS Multilingual (English / Hindi among others) |

### Languages (sidebar: Voice language)

Auto detect, English (`en-IN`), Hindi (`hi-IN`), Marathi (`mr-IN`), Telugu (`te-IN`), Kannada (`kn-IN`), Malayalam (`ml-IN`), Tamil (`ta-IN`), Punjabi (`pa-IN`), Odia (`od-IN`), Bengali (`bn-IN`), Gujarati (`gu-IN`).

The chat system prompt replies in the selected / detected language.

### Environment

Required for Indian-language hear + speak
    SARVAM_API_KEY= ---
    SARVAM_STT_MODEL=saaras:v4  
    SARVAM_TTS_MODEL=bulbul:v3
    VOICE_STT_PROVIDER=sarvam
    VOICE_TTS_PROVIDER=sarvam

Optional NVIDIA speech fallback
    NVIDIA_STT_MODEL=nvidia/parakeet-ctc-1.1b-asr 
    NVIDIA_STT_LANGUAGE=en-US NVIDIA_TTS_VOICE=Magpie-Multilingual.EN-US.Aria
    NVIDIA_TTS_LANGUAGE=en-US 
    
