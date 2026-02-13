"""Multiple entry points using the same worker server.

Demonstrates three different ways to interact with the same worker:
- WebSocket /ws — standard real-time chat
- POST /api/summarize — REST endpoint that dispatches a summarization handler
- POST /api/notify — inject a system notification into a thread
"""

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
        assistant_name="Multi Bot",
    )
)

WORKER_ID = "multi-worker"
ORG_ID = "org_demo"
WS_ID = "ws_demo"


# --- Handlers ---


async def chat_handler(on, send, memory, analytics, settings):
    """Standard chat handler for WebSocket connections."""
    yield send.run_start(agent_id="chat")
    yield send.text(f"You said: {on.message}")
    yield send.update_thread(title=on.message[:60])
    yield send.run_end()


async def summarize_handler(on, send, memory, analytics, settings):
    """Summarization handler — reads history and produces a summary."""
    yield send.run_start(agent_id="summarizer")

    history = await memory.get_history(limit=50)
    message_count = len(history)

    # In a real app, you'd pass history to an LLM for summarization
    summary = f"Thread summary: {message_count} messages. Latest: {on.message[:80]}"

    yield send.text(summary)
    yield send.update_thread(title="Summary")
    yield send.run_end()


async def notify_handler(on, send, memory, analytics, settings):
    """Simple notification handler — no LLM, just injects a system message."""
    yield send.run_start(agent_id="notifier")
    yield send.text(on.message, author="System")
    yield send.run_end()


# --- Endpoints ---


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, chat_handler)


@app.post("/api/summarize")
async def summarize(request: Request):
    """Dispatch summarization on an existing thread, return collected text."""
    body = await request.json()
    thread_id = body.get("thread_id")
    if not thread_id:
        return JSONResponse({"error": "thread_id required"}, status_code=400)

    stream = await worker_server.dispatch(
        handler=summarize_handler,
        worker_id=WORKER_ID,
        organization_id=ORG_ID,
        workspace_id=WS_ID,
        message="Summarize this conversation",
        user_id="api",
        thread_id=thread_id,
    )

    texts = []
    async for event in stream:
        if event.send_type == "text":
            texts.append(event.data["text"])

    return {"thread_id": stream.thread_id, "summary": " ".join(texts)}


@app.post("/api/notify")
async def notify(request: Request):
    """Inject a system notification into a thread."""
    body = await request.json()
    thread_id = body.get("thread_id")
    message = body.get("message", "")
    if not thread_id or not message:
        return JSONResponse({"error": "thread_id and message required"}, status_code=400)

    stream = await worker_server.dispatch(
        handler=notify_handler,
        worker_id=WORKER_ID,
        organization_id=ORG_ID,
        workspace_id=WS_ID,
        message=message,
        user_id="system",
        thread_id=thread_id,
    )

    await stream.collect()

    return {"status": "ok", "thread_id": stream.thread_id}


@app.on_event("startup")
async def startup():
    await worker_server.ensure_initialized()


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
