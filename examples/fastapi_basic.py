from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
    )
)


async def echo_agent(request, response, memory):
    yield response.run_start(agent_id="echo")
    yield response.text(f"You said: {request.message}")
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, echo_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
