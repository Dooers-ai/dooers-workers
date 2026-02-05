from dataclasses import dataclass
from typing import Literal


@dataclass
class WorkerEvent:
    response_type: str
    data: dict


class WorkerResponse:
    def text(self, text: str) -> WorkerEvent:
        return WorkerEvent(
            response_type="text",
            data={"text": text},
        )

    def image(
        self,
        url: str,
        mime_type: str | None = None,
        alt: str | None = None,
    ) -> WorkerEvent:
        return WorkerEvent(
            response_type="image",
            data={"url": url, "mime_type": mime_type, "alt": alt},
        )

    def document(
        self,
        url: str,
        filename: str,
        mime_type: str,
    ) -> WorkerEvent:
        return WorkerEvent(
            response_type="document",
            data={"url": url, "filename": filename, "mime_type": mime_type},
        )

    def tool_call(self, name: str, args: dict) -> WorkerEvent:
        return WorkerEvent(
            response_type="tool_call",
            data={"name": name, "args": args},
        )

    def tool_result(self, name: str, result: dict) -> WorkerEvent:
        return WorkerEvent(
            response_type="tool_result",
            data={"name": name, "result": result},
        )

    def run_start(self, agent_id: str | None = None) -> WorkerEvent:
        return WorkerEvent(
            response_type="run_start",
            data={"agent_id": agent_id},
        )

    def run_end(
        self,
        status: Literal["succeeded", "failed"] = "succeeded",
        error: str | None = None,
    ) -> WorkerEvent:
        return WorkerEvent(
            response_type="run_end",
            data={"status": status, "error": error},
        )
