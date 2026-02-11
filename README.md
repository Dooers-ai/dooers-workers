# dooers-workers

dooers.ai SDK for creating agent workers

## Install

```bash
pip install dooers-workers
```

Or from source:

```bash
pip install git+https://github.com/dooers/dooers-workers.git
```

## Usage

```python
from fastapi import FastAPI, WebSocket
from openai import AsyncOpenAI
from dooers import WorkerServer, WorkerConfig

app = FastAPI()
openai = AsyncOpenAI()

worker_server = WorkerServer(WorkerConfig(
    database_type="sqlite",
    database_name="worker.db",
    assistant_name="My Assistant",  # Display name for assistant responses
))


async def agent_handler(request, response, memory, analytics, settings):
    yield response.run_start()

    history = await memory.get_history(limit=20, format="openai")

    completion = await openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            *history,
        ],
    )

    yield response.text(completion.choices[0].message.content)
    # Or override author for specific messages:
    # yield response.text("Hello!", author="Support Bot")
    yield response.run_end()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, agent_handler)
```

## agent_handler

```python
async def agent_handler(request, response, memory, analytics, settings):
    ...
```

### Request

```python
request.message      # str
request.content      # list[ContentPart]
request.thread_id    # str
request.event_id     # str
request.user_id      # str | None
request.user_name    # str | None
request.user_email   # str | None
```

### Memory

```python
# Latest 50 messages in chronological order (default)
messages = await memory.get_history(limit=50)

# Oldest 50 messages in chronological order
messages = await memory.get_history(limit=50, order="asc")

# Format for different LLM providers
messages = await memory.get_history(format="openai")
messages = await memory.get_history(format="anthropic")
messages = await memory.get_history(format="google")
messages = await memory.get_history(format="cohere")
messages = await memory.get_history(format="voyage")

# Filter by event fields
messages = await memory.get_history(filters={"user_email": "alice@example.com"})
messages = await memory.get_history(filters={"actor": "user"})

# Raw ThreadEvent objects
events = await memory.get_history_raw(limit=50)
events = await memory.get_history_raw(limit=50, order="desc", filters={"type": "message"})
```

### Response

```python
# Messages - with optional author override
yield response.text("Hello, I'm your assistant.")
yield response.text("Hello!", author="Support Bot")  # Override default assistant_name
yield response.image(url, mime_type?, alt?, author?)
yield response.document(url, filename, mime_type, author?)

# Tool calls - with correlation ID for frontend rendering
call_id = str(uuid.uuid4())
yield response.tool_call(name, args, display_name?, id?)
yield response.tool_result(name, result, args?, display_name?, id?)

# Example: correlated tool call
yield response.tool_call("search", {"q": "hello"}, display_name="Searching...", id=call_id)
yield response.tool_result("search", {"results": [...]}, args={"q": "hello"}, id=call_id)

# Run lifecycle
yield response.run_start(agent_id?)
yield response.run_end(status?, error?)
```

### Analytics

```python
await analytics.track("event.name", data={"key": "value"})
await analytics.like("event", target_id, reason?)
await analytics.dislike("event", target_id, reason?)
```

### Settings

```python
value = await settings.get("field_id")
all_values = await settings.get_all()
await settings.set("field_id", new_value)
```

## worker_server

### Persistence

Direct database access for threads, events, and settings.

```python

persistence = worker_server.persistence

thread = await persistence.get_thread(thread_id)
await persistence.create_thread(thread)
await persistence.update_thread(thread)
threads = await persistence.list_threads(worker_id, user_id, cursor, limit)

await persistence.create_event(event)
events = await persistence.get_events(thread_id, limit=50, order="asc")
events = await persistence.get_events(thread_id, order="desc", filters={"actor": "user"})

settings = await persistence.get_settings(worker_id)
await persistence.update_setting(worker_id, field_id, value)
```

### Broadcast

Pushes events to WebSocket subscribers. `send_event` also persists the event and updates the thread timestamp.

```python

broadcast = worker_server.broadcast

# Persist + broadcast a message (returns event, connections notified)
event, count = await broadcast.send_event(worker_id, thread_id, content, actor)

# Broadcast a thread change (no persistence, notification only)
count = await broadcast.send_thread_update(worker_id, thread)

# Create thread + broadcast (convenience)
thread, count = await broadcast.create_thread_and_broadcast(worker_id, user_id, title)
```

## Settings Schema

Define configurable settings for your worker:

```python
from dooers import (
    WorkerConfig,
    WorkerServer,
    SettingsSchema,
    SettingsField,
    SettingsFieldType,
    SettingsSelectOption,
)

schema = SettingsSchema(
    fields=[
        SettingsField(
            id="model",
            type=SettingsFieldType.SELECT,
            label="Model",
            value="gpt-4o-mini",
            options=[
                SettingsSelectOption(value="gpt-4o-mini", label="GPT-4o Mini"),
                SettingsSelectOption(value="gpt-4o", label="GPT-4o"),
            ],
        ),
        SettingsField(
            id="temperature",
            type=SettingsFieldType.NUMBER,
            label="Temperature",
            value=0.7,
            min=0,
            max=2,
        ),
    ]
)

worker_server = WorkerServer(WorkerConfig(
    database_type="sqlite",
    database_name="worker.db",
    assistant_name="Settings Bot",
    settings_schema=schema,
))
```

### Field Types

- `TEXT` - Single-line text input
- `NUMBER` - Numeric input with optional min/max
- `SELECT` - Dropdown selection
- `CHECKBOX` - Boolean toggle
- `TEXTAREA` - Multi-line text input
- `PASSWORD` - Password input (hidden)
- `EMAIL` - Email input
- `DATE` - Date picker
- `IMAGE` - Display-only image (e.g., QR codes)
