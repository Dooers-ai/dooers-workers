import httpx
from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
    )
)


async def search_web(query: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json"},
        )
        return resp.json()


async def tool_agent(request, response, memory):
    yield response.run_start(agent_id="tool-agent")

    if request.message.lower().startswith("search "):
        query = request.message[7:]
        yield response.tool_call("web_search", {"query": query})
        results = await search_web(query)
        yield response.tool_result("web_search", {"results": results})

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
