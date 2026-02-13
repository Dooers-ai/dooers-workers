import json
import uuid

from agents import Agent, Runner, function_tool
from fastapi import FastAPI, WebSocket

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_type="sqlite",
        database_name="worker.db",
        assistant_name="OpenAI Agent",
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


async def openai_agents_handler(on, send, memory, analytics, settings):
    yield send.run_start(agent_id="openai-agents")

    history = await memory.get_history(limit=20)

    input_messages = []
    for event in history:
        if event.type == "message" and event.content:
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if text:
                role = "user" if event.actor == "user" else "assistant"
                input_messages.append({"role": role, "content": text})

    input_messages.append({"role": "user", "content": on.message})

    result = await Runner.run(agent, input=input_messages)

    # Track tool call IDs for correlation
    tool_call_ids: dict[str, str] = {}

    for item in result.new_items:
        if item.type == "tool_call_item":
            call_id = str(uuid.uuid4())
            args = json.loads(item.raw_item.arguments)
            tool_call_ids[item.raw_item.call_id] = call_id
            yield send.tool_call(
                item.raw_item.name,
                args,
                display_name=f"Calling {item.raw_item.name}...",
                id=call_id,
            )
        elif item.type == "tool_call_output_item":
            call_id = tool_call_ids.get(item.raw_item.call_id)
            yield send.tool_result(
                item.raw_item.name,
                {"output": item.output},
                id=call_id,
            )

    yield send.text(result.final_output)
    yield send.update_thread(title=on.message[:60])
    yield send.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, openai_agents_handler)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
