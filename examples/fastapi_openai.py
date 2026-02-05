import os

from fastapi import FastAPI, WebSocket
from openai import AsyncOpenAI

from dooers import (
    SettingsField,
    SettingsFieldType,
    SettingsSchema,
    SettingsSelectOption,
    WorkerConfig,
    WorkerServer,
)

app = FastAPI()

# Define settings schema for this worker
settings_schema = SettingsSchema(
    fields=[
        SettingsField(
            id="model",
            type=SettingsFieldType.SELECT,
            label="Model",
            value="gpt-4o-mini",
            options=[
                SettingsSelectOption(value="gpt-4o-mini", label="GPT-4o Mini"),
                SettingsSelectOption(value="gpt-4o", label="GPT-4o"),
                SettingsSelectOption(value="gpt-4-turbo", label="GPT-4 Turbo"),
            ],
        ),
        SettingsField(
            id="temperature",
            type=SettingsFieldType.NUMBER,
            label="Temperature",
            value=0.7,
            min=0,
            max=2,
        ),
        SettingsField(
            id="system_prompt",
            type=SettingsFieldType.TEXTAREA,
            label="System Prompt",
            value="You are a helpful assistant.",
            rows=4,
        ),
    ]
)

worker_server = WorkerServer(
    WorkerConfig(
        database_url="sqlite:///worker.db",
        database_type="sqlite",
        settings_schema=settings_schema,
    )
)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def openai_agent(request, response, memory, analytics, settings):
    yield response.run_start(agent_id="openai-gpt")

    # Get settings
    model = await settings.get("model")
    temperature = await settings.get("temperature")
    system_prompt = await settings.get("system_prompt")

    # Track custom event
    await analytics.track("llm.request", data={"model": model})

    # Get formatted history (defaults to OpenAI format)
    history = await memory.get_history(limit=20)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": request.message})

    completion = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=messages,
    )

    # Track token usage
    await analytics.track(
        "llm.response",
        data={
            "model": model,
            "tokens": completion.usage.total_tokens if completion.usage else None,
        },
    )

    yield response.text(completion.choices[0].message.content)
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, openai_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
