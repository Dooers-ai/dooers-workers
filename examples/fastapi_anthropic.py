import os

from anthropic import AsyncAnthropic
from fastapi import FastAPI, WebSocket

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
            value="claude-sonnet-4-20250514",
            options=[
                SettingsSelectOption(value="claude-sonnet-4-20250514", label="Claude Sonnet 4"),
                SettingsSelectOption(value="claude-opus-4-20250514", label="Claude Opus 4"),
                SettingsSelectOption(value="claude-haiku-4-20250514", label="Claude Haiku 4"),
            ],
        ),
        SettingsField(
            id="max_tokens",
            type=SettingsFieldType.NUMBER,
            label="Max Tokens",
            value=1024,
            min=1,
            max=4096,
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
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def anthropic_agent(request, response, memory, analytics, settings):
    yield response.run_start(agent_id="anthropic-claude")

    # Get settings
    model = await settings.get("model")
    max_tokens = await settings.get("max_tokens")
    system_prompt = await settings.get("system_prompt")

    # Track custom event
    await analytics.track("llm.request", data={"model": model})

    # Get formatted history (Anthropic uses same format as OpenAI)
    history = await memory.get_history(limit=20, format="anthropic")

    messages = list(history)
    messages.append({"role": "user", "content": request.message})

    result = await client.messages.create(
        model=model,
        max_tokens=int(max_tokens),
        system=system_prompt,
        messages=messages,
    )

    # Track token usage
    await analytics.track(
        "llm.response",
        data={
            "model": model,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
    )

    yield response.text(result.content[0].text)
    yield response.run_end()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await worker_server.handle(websocket, anthropic_agent)


@app.on_event("shutdown")
async def shutdown():
    await worker_server.close()
