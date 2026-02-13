from dataclasses import dataclass, field
from datetime import datetime

from dooers.protocol.models import ContentPart


@dataclass
class WorkerOn:
    message: str
    content: list[ContentPart]
    thread_id: str
    event_id: str
    organization_id: str
    workspace_id: str
    user_id: str
    user_name: str
    user_email: str
    user_role: str
    thread_title: str | None = field(default=None)
    thread_created_at: datetime | None = field(default=None)
