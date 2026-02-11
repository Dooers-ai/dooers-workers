import uuid

import httpx
from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
        assistant_name="Search Agent",
    )
)


async def search_web(query: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json"},
        )
        return resp.json()


async def tool_agent(request, response, memory, analytics, settings):
    yield response.run_start(agent_id="tool-agent")

    if request.message.lower().startswith("search "):
        query = request.message[7:]
        args = {"query": query}

        # Generate correlation ID for tool call/result pairing
        call_id = str(uuid.uuid4())

        # Emit tool call with display_name for frontend rendering
        yield response.tool_call(
            "web_search",
            args,
            display_name="Searching the web...",
            id=call_id,
        )

        results = await search_web(query)

        # Emit tool result with same ID for correlation
        yield response.tool_result(
            "web_search",
            {"results": results},
            args=args,  # Echo args for self-contained rendering
            id=call_id,
        )

        if results.get("Abstract"):
            yield response.text(results["Abstract"])
        else:
            yield response.text(f"No results found for: {query}")
    else:
        yield response.text("Try: search <query>")

    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, tool_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
