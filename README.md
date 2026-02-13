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


async def agent_handler(on, send, memory, analytics, settings):
    yield send.run_start()

    history = await memory.get_history(limit=20, format="openai")

    completion = await openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            *history,
        ],
    )

    yield send.text(completion.choices[0].message.content)
    yield send.update_thread(title=on.message[:60])
    yield send.run_end()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, agent_handler)
```

## Handler

```python
async def agent_handler(on, send, memory, analytics, settings):
    ...
```

### on

Incoming message context.

```python
on.message           # str — extracted text from content parts
on.content           # list[ContentPart]
on.thread_id         # str
on.event_id          # str
on.organization_id   # str
on.workspace_id      # str
on.user_id           # str
on.user_name         # str
on.user_email        # str
on.user_role         # str
on.thread_title      # str | None
on.thread_created_at # datetime | None
```

### send

Yield events back to the client.

```python
# Messages — with optional author override
yield send.text("Hello, I'm your assistant.")
yield send.text("Hello!", author="Support Bot")  # Override default assistant_name
yield send.image(url, mime_type?, alt?, author?)
yield send.document(url, filename, mime_type, author?)

# Tool calls — with correlation ID for frontend rendering
call_id = str(uuid.uuid4())
yield send.tool_call(name, args, display_name?, id?)
yield send.tool_result(name, result, args?, display_name?, id?)

# Thread metadata
yield send.update_thread(title="New title")

# Run lifecycle
yield send.run_start(agent_id?)
yield send.run_end(status?, error?)
```

### memory

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

### analytics

```python
await analytics.track("event.name", data={"key": "value"})
await analytics.like("event", target_id, reason?)
await analytics.dislike("event", target_id, reason?)
```

### settings

```python
value = await settings.get("field_id")
all_values = await settings.get_all()
all_values = await settings.get_all(exclude=["avatar_base64"])  # Strip large fields
await settings.set("field_id", new_value)
```

## Dispatch

Run handlers programmatically from REST endpoints, webhooks, or background jobs — without a WebSocket connection.

```python
stream = await worker_server.dispatch(
    handler=my_handler,
    worker_id="worker-1",
    organization_id="org-1",
    workspace_id="ws-1",
    message="Hello from webhook",
    user_id="user-1",
    user_name="Alice",
    thread_id=None,           # None → creates new thread
    thread_title="Webhook",   # Title for new threads
)

# Properties available immediately (before iteration)
stream.thread_id       # str
stream.event_id        # str
stream.is_new_thread   # bool

# Stream events
async for event in stream:
    if event.send_type == "text":
        print(event.data["text"])

# Or collect all events at once
events = await stream.collect()
```

## Repository

CRUD interface for threads, events, runs, and settings.

```python
repo = await worker_server.repository()

# Threads
threads = await repo.list_threads(filter={"worker_id": "w1", "organization_id": "org1", "workspace_id": "ws1"})
thread = await repo.get_thread(thread_id)
thread = await repo.create_thread(worker_id, organization_id, workspace_id, user_id, title?)
thread = await repo.update_thread(thread_id, title="New title")
await repo.remove_thread(thread_id)

# Events
events = await repo.list_events(filter={"thread_id": "t1"}, order={"direction": "asc"})
event = await repo.get_event(event_id)
event = await repo.create_event(thread_id, type="message", actor="user", content=[...])
await repo.remove_event(event_id)

# Runs
runs = await repo.list_runs(filter={"thread_id": "t1"})
run = await repo.get_run(run_id)

# Settings
values = await repo.get_settings(worker_id)
await repo.update_settings(worker_id, {"key": "value"})
```

## Standalone Utilities

Access memory, settings, and analytics outside of handlers.

```python
memory = await worker_server.memory(thread_id)
history = await memory.get_history(limit=20, format="openai")

settings = await worker_server.settings(worker_id)
value = await settings.get("model")

analytics = await worker_server.analytics(worker_id, thread_id="t1")
await analytics.track("custom.event")
```

## Settings Schema

Define configurable settings for your worker.

```python
from dooers import (
    WorkerConfig,
    WorkerServer,
    SettingsSchema,
    SettingsField,
    SettingsFieldGroup,
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
        SettingsFieldGroup(
            id="advanced",
            label="Advanced Settings",
            collapsible="closed",
            fields=[
                SettingsField(
                    id="temperature",
                    type=SettingsFieldType.NUMBER,
                    label="Temperature",
                    value=0.7,
                    min=0,
                    max=2,
                ),
            ],
        ),
        SettingsField(
            id="api_key",
            type=SettingsFieldType.PASSWORD,
            label="API Key",
            is_internal=True,  # Hidden from frontend, backend-only
        ),
    ]
)

worker_server = WorkerServer(WorkerConfig(
    database_type="sqlite",
    database_name="worker.db",
    settings_schema=schema,
))
```

### Field Types

- `TEXT` — Single-line text input
- `NUMBER` — Numeric input with optional min/max
- `SELECT` — Dropdown selection
- `CHECKBOX` — Boolean toggle
- `TEXTAREA` — Multi-line text input
- `PASSWORD` — Password input (hidden)
- `EMAIL` — Email input
- `DATE` — Date picker
- `IMAGE` — Display-only image (e.g., QR codes)

### Field Groups

Groups organize related fields with an optional collapsible UI:

```python
SettingsFieldGroup(
    id="group_id",
    label="Group Label",
    collapsible="open",    # "open" | "closed" | None (not collapsible)
    is_internal=False,     # Hide entire group from frontend
    fields=[...],
)
```

### Internal Fields

Fields with `is_internal=True` are:
- Hidden from frontend settings snapshots
- Rejected if a WebSocket client attempts to patch them
- Suppressed from broadcast patch notifications
- Accessible normally via `settings.get()` and `settings.get_all()` in handlers

## Database Configuration

The SDK supports three database backends: PostgreSQL, SQLite, and Azure Cosmos DB.

### PostgreSQL (default)

```python
worker_server = WorkerServer(WorkerConfig(
    database_type="postgres",
    database_host="localhost",
    database_port=5432,
    database_user="postgres",
    database_name="mydb",
    database_password="secret",
    database_ssl=False,  # or "require", "verify-full", etc.
    database_table_prefix="worker_",
    database_auto_migrate=True,
))
```

### SQLite

```python
worker_server = WorkerServer(WorkerConfig(
    database_type="sqlite",
    database_name="worker.db",
    database_table_prefix="worker_",
    database_auto_migrate=True,
))
```

### Azure Cosmos DB

Requires the cosmos extra: `pip install dooers-workers[cosmos]`

```python
worker_server = WorkerServer(WorkerConfig(
    database_type="cosmos",
    database_host="https://your-account.documents.azure.com:443/",  # endpoint
    database_name="your-database",
    database_key="your-cosmos-key",
    database_table_prefix="worker_",
    database_auto_migrate=True,
))
```

### Environment Variables

All database fields can be configured via environment variables:

| Field | Environment Variable |
|-------|---------------------|
| `database_host` | `WORKER_DATABASE_HOST` |
| `database_port` | `WORKER_DATABASE_PORT` |
| `database_user` | `WORKER_DATABASE_USER` |
| `database_name` | `WORKER_DATABASE_NAME` |
| `database_password` | `WORKER_DATABASE_PASSWORD` |
| `database_key` | `WORKER_DATABASE_KEY` |
| `database_ssl` | `WORKER_DATABASE_SSL` |

## Thread Privacy

By default, all users connected to a worker can see all threads (team collaboration mode). Enable `private_threads` to restrict users to only their own threads:

```python
worker_server = WorkerServer(WorkerConfig(
    database_type="postgres",
    database_name="mydb",
    private_threads=True,  # Users only see their own threads
))
```

When `private_threads=True`:
- Thread listing (`thread.list`) filters by the connected user's `user_id`
- Each user only sees threads they created
- Useful for multi-tenant or personal assistant scenarios
