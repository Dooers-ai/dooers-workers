import os

from fastapi import FastAPI, WebSocket
from google import genai
from google.genai import types

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_url="sqlite:///worker.db",
        database_type="sqlite",
    )
)
client = genai.Client(
    vertexai=True,
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
)


async def vertex_agent(request, response, memory):
    yield response.run_start(agent_id="vertex-gemini")

    history = await memory.get_history(limit=20)

    contents = []
    for event in history:
        if event.type == "message" and event.content:
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if text:
                role = "user" if event.actor == "user" else "model"
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text)]))

    contents.append(types.Content(role="user", parts=[types.Part.from_text(request.message)]))

    result = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="You are a helpful assistant.",
        ),
    )

    yield response.text(result.text)
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, vertex_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
