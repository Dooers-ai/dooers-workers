from typing import TYPE_CHECKING, Any, Literal

from dooers.protocol.models import ThreadEvent

if TYPE_CHECKING:
    from dooers.persistence.base import Persistence

HistoryFormat = Literal["openai", "anthropic", "google", "cohere", "voyage"]


class WorkerMemory:
    def __init__(self, thread_id: str, persistence: "Persistence"):
        self._thread_id = thread_id
        self._persistence = persistence

    async def get_history_raw(self, limit: int = 50) -> list[ThreadEvent]:
        """
        Get raw conversation history as ThreadEvent objects.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of ThreadEvent objects
        """
        return await self._persistence.get_events(
            thread_id=self._thread_id,
            after_event_id=None,
            limit=limit,
        )

    async def get_history(
        self,
        limit: int = 50,
        format: HistoryFormat = "openai",
    ) -> list[dict[str, Any]]:
        """
        Get conversation history formatted for LLM APIs.

        Args:
            limit: Maximum number of events to return
            format: Output format - 'openai', 'anthropic', 'google', 'cohere', 'voyage'

        Returns:
            List of message dicts in the specified format

        Format examples:
            openai:    {"role": "user/assistant", "content": "..."}
            anthropic: {"role": "user/assistant", "content": "..."}
            google:    {"role": "user/model", "parts": [{"text": "..."}]}
            cohere:    {"role": "USER/CHATBOT", "message": "..."}
            voyage:    {"role": "user/assistant", "content": "..."}
        """
        events = await self.get_history_raw(limit)
        messages: list[dict[str, Any]] = []

        for event in events:
            if event.type != "message" or not event.content:
                continue

            # Extract text from content parts
            text = " ".join(part.text for part in event.content if hasattr(part, "text"))
            if not text:
                continue

            is_user = event.actor == "user"
            message = self._format_message(text, is_user, format)
            messages.append(message)

        return messages

    def _format_message(
        self,
        text: str,
        is_user: bool,
        format: HistoryFormat,
    ) -> dict[str, Any]:
        """Format a single message for the specified provider."""
        match format:
            case "openai" | "voyage":
                return {
                    "role": "user" if is_user else "assistant",
                    "content": text,
                }
            case "anthropic":
                return {
                    "role": "user" if is_user else "assistant",
                    "content": text,
                }
            case "google":
                return {
                    "role": "user" if is_user else "model",
                    "parts": [{"text": text}],
                }
            case "cohere":
                return {
                    "role": "USER" if is_user else "CHATBOT",
                    "message": text,
                }
            case _:
                # Default to OpenAI format
                return {
                    "role": "user" if is_user else "assistant",
                    "content": text,
                }
