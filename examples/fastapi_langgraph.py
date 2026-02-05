import os

from fastapi import FastAPI, WebSocket
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from dooers import WorkerConfig, WorkerServer

app = FastAPI()
worker_server = WorkerServer(
    WorkerConfig(
        database_url="sqlite:///worker.db",
        database_type="sqlite",
    )
)

llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=os.getenv("OPENAI_API_KEY"),
)


@tool
def multiply(a: int, b: int) -> int:
    return a * b


@tool
def add(a: int, b: int) -> int:
    return a + b


agent = create_react_agent(llm, tools=[multiply, add])


async def langgraph_agent(request, response, memory):
    yield response.run_start(agent_id="langgraph")

    history = await memory.get_history(limit=20)

    messages = [SystemMessage(content="You are a helpful assistant with math tools.")]
    for event in history:
        if event.type == "message" and event.content:
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if text:
                if event.actor == "user":
                    messages.append(HumanMessage(content=text))
                else:
                    messages.append(AIMessage(content=text))

    messages.append(HumanMessage(content=request.message))

    result = await agent.ainvoke({"messages": messages})

    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                yield response.tool_call(tc["name"], tc["args"])
        if msg.type == "tool":
            yield response.tool_result(msg.name, {"result": msg.content})

    final = result["messages"][-1]
    if final.content:
        yield response.text(final.content)

    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, langgraph_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
