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
    database_url="sqlite:///worker.db",
    database_type="sqlite",
))


async def agent_handler(request, response, memory, analytics, settings):
    yield response.run_start()

    # Track custom event
    await analytics.track("llm.called", data={"model": "gpt-4o-mini"})

    completion = await openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": request.message}],
    )

    yield response.text(completion.choices[0].message.content)
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
```

### Memory

```python
# Get formatted history (defaults to OpenAI format)
messages = await memory.get_history(limit=50)

# Specify format for different providers
messages = await memory.get_history(format="openai")
messages = await memory.get_history(format="anthropic")
messages = await memory.get_history(format="google")
messages = await memory.get_history(format="cohere")
messages = await memory.get_history(format="voyage")

# Get raw ThreadEvent objects
events = await memory.get_history_raw(limit=50)
```

### Response

```python
yield response.text("Hello, I'm your assistant.")
yield response.image(url, mime_type?, alt?)
yield response.document(url, filename, mime_type)
yield response.tool_call(name, args)
yield response.tool_result(name, result)
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
events = await persistence.get_events(thread_id, after_event_id, limit)

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
    database_url="sqlite:///worker.db",
    database_type="sqlite",
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


