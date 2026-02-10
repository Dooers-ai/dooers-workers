from datetime import UTC, datetime

from fastapi import FastAPI, Request, WebSocket

from dooers import TextPart, Thread, WorkerConfig, WorkerServer

worker_server = WorkerServer(WorkerConfig(database_type="sqlite", database_name="worker.db"))

app = FastAPI()


async def handler(request, response, memory, analytics, settings):
    yield response.run_start(agent_id="echo")
    yield response.text(f"Echo: {request.message}")
    yield response.run_end()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, handler)


@app.post("/webhook/{worker_id}")
async def webhook(worker_id: str, request: Request):
    body = await request.json()
    sender = body.get("sender", "anonymous")
    text = body.get("text", "")
    thread_id = f"webhook_{worker_id}_{sender}"

    persistence = worker_server.persistence
    broadcast = worker_server.broadcast

    # Create thread if it doesn't exist
    thread = await persistence.get_thread(thread_id)
    if not thread:
        now = datetime.now(UTC)
        thread = Thread(
            id=thread_id,
            worker_id=worker_id,
            user_id=sender,
            title=sender,
            created_at=now,
            updated_at=now,
            last_event_at=now,
        )
        await persistence.create_thread(thread)
        await broadcast.send_thread_update(worker_id, thread)

    # Persist user message + notify WebSocket clients
    await broadcast.send_event(
        worker_id=worker_id,
        thread_id=thread_id,
        content=[TextPart(text=text)],
        actor="user",
        user_id=sender,
    )

    # Persist assistant reply + notify WebSocket clients
    reply = f"Echo: {text}"
    await broadcast.send_event(
        worker_id=worker_id,
        thread_id=thread_id,
        content=[TextPart(text=reply)],
        actor="assistant",
    )

    return {"status": "ok", "thread_id": thread_id}


@app.on_event("startup")
async def startup():
    await worker_server.ensure_initialized()


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
