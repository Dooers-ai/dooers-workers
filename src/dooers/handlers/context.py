from dataclasses import dataclass, field
from datetime import datetime

from dooers.protocol.models import Metadata


@dataclass
class WorkerContext:
    """Contextual metadata for the incoming message.

    Provides "where and who" information via the metadata field,
    plus thread-specific context.

    Always present in handlers and dispatch flows.
    """

    thread_id: str
    event_id: str
    metadata: Metadata = field(default_factory=Metadata)
    thread_title: str | None = field(default=None)
    thread_created_at: datetime | None = field(default=None)
