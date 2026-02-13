from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
        assistant_name="Echo Bot",  # Default display name for assistant messages
    )
)


async def echo_agent(on, send, memory, analytics, settings):
    yield send.run_start(agent_id="echo")
    # Uses default assistant_name ("Echo Bot") as author
    yield send.text(f"You said: {on.message}")
    # Or override author for specific messages:
    # yield send.text("System notice", author="System")
    yield send.update_thread(title=on.message[:60])
    yield send.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, echo_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
