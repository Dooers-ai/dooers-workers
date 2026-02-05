import os

from fastapi import FastAPI, WebSocket
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

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


async def langchain_agent(request, response, memory):
    yield response.run_start(agent_id="langchain")

    history = await memory.get_history(limit=20)

    messages = [SystemMessage(content="You are a helpful assistant.")]
    for event in history:
        if event.type == "message" and event.content:
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if text:
                if event.actor == "user":
                    messages.append(HumanMessage(content=text))
                else:
                    messages.append(AIMessage(content=text))

    messages.append(HumanMessage(content=request.message))

    result = await llm.ainvoke(messages)

    yield response.text(result.content)
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, langchain_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
