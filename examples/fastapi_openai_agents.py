import json

from agents import Agent, Runner, function_tool
from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_url="sqlite:///worker.db",
        database_type="sqlite",
    )
)


@function_tool
def get_weather(city: str) -> str:
    """Get the weather for a city."""
    return f"The weather in {city} is sunny, 22°C."


agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    tools=[get_weather],
)


async def openai_agents_handler(request, response, memory):
    yield response.run_start(agent_id="openai-agents")

    history = await memory.get_history(limit=20)

    input_messages = []
    for event in history:
        if event.type == "message" and event.content:
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if text:
                role = "user" if event.actor == "user" else "assistant"
                input_messages.append({"role": role, "content": text})

    input_messages.append({"role": "user", "content": request.message})

    result = await Runner.run(agent, input=input_messages)

    for item in result.new_items:
        if item.type == "tool_call_item":
            yield response.tool_call(
                item.raw_item.name,
                json.loads(item.raw_item.arguments),
            )
        elif item.type == "tool_call_output_item":
            yield response.tool_result("tool", {"output": item.output})

    yield response.text(result.final_output)
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, openai_agents_handler)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
