"""WhatsApp webhook using the dispatch() API.

Demonstrates an external webhook flow:
- Simulated DB lookup to resolve phone → organization/workspace
- Uses repo.list_threads() to find or create threads per phone/worker
- Dispatches a handler via worker_server.dispatch()
- Streams events back, forwarding text replies to a simulated WhatsApp API
- Uses send.update_thread(title=...) for descriptive thread titles
"""

import logging

from fastapi import FastAPI, Request, WebSocket

from dooers import WorkerConfig, WorkerServer

logger = logging.getLogger(__name__)

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
        assistant_name="WhatsApp Bot",
    )
)

# --- Simulated external services ---

CUSTOMER_DB: dict[str, dict] = {
    "+5511999990001": {
        "organization_id": "org_acme",
        "workspace_id": "ws_support",
        "name": "Alice",
    },
    "+5511999990002": {
        "organization_id": "org_acme",
        "workspace_id": "ws_support",
        "name": "Bob",
    },
}


def get_customer(phone: str) -> dict | None:
    """Simulated DB lookup: phone → customer record."""
    return CUSTOMER_DB.get(phone)


async def send_whatsapp_message(phone: str, text: str) -> None:
    """Simulated WhatsApp API call (replace with real Evolution API / Cloud API)."""
    logger.info("[whatsapp] → %s: %s", phone, text[:80])


# --- Handler ---


async def whatsapp_handler(on, send, memory, analytics, settings):
    yield send.run_start(agent_id="whatsapp-echo")
    yield send.text(f"Echo: {on.message}")
    yield send.update_thread(title=on.message[:60])
    yield send.run_end()


# --- Webhook endpoint ---


@app.post("/webhook/{worker_id}")
async def webhook(worker_id: str, request: Request):
    body = await request.json()
    phone = body.get("phone", "")
    text = body.get("text", "")

    if not phone or not text:
        return {"status": "ignored"}

    # 1. Look up customer by phone number
    customer = get_customer(phone)
    if not customer:
        return {"status": "unknown_customer"}

    organization_id = customer["organization_id"]
    workspace_id = customer["workspace_id"]
    customer_name = customer.get("name", phone)

    # 2. Find existing thread for this phone/worker
    repo = await worker_server.repository()
    threads = await repo.list_threads(
        filter={
            "worker_id": worker_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "user_id": phone,
        },
        limit=1,
    )
    thread_id = threads[0].id if threads else None

    # 3. Dispatch handler
    stream = await worker_server.dispatch(
        handler=whatsapp_handler,
        worker_id=worker_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        message=text,
        user_id=phone,
        user_name=customer_name,
        thread_id=thread_id,
        thread_title=f"WhatsApp: {customer_name}" if not thread_id else None,
    )

    # 4. Stream events and forward text replies to WhatsApp
    async for event in stream:
        if event.send_type == "text":
            await send_whatsapp_message(phone, event.data["text"])

    return {"status": "ok", "thread_id": stream.thread_id, "is_new": stream.is_new_thread}


# --- WebSocket for dashboard monitoring ---


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, whatsapp_handler)


@app.on_event("startup")
async def startup():
    await worker_server.ensure_initialized()


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
