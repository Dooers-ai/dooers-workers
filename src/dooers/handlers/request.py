from dataclasses import dataclass

from dooers.protocol.models import ContentPart


@dataclass
class WorkerRequest:
    message: str
    content: list[ContentPart]
    thread_id: str
    event_id: str
    user_id: str | None
    user_name: str | None
    user_email: str | None
