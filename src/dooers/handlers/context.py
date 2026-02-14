from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkerContext:
    """Contextual metadata for the incoming message.

    Provides "where and who" information:
    - Where: thread_id, organization_id, workspace_id
    - Who: user_id, user_name, user_email, user_role
    - When: thread_created_at

    Always present in handlers and dispatch flows.
    """

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
