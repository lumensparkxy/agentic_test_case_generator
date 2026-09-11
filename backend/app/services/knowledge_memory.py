"""Read-only ADK adapter, bound to an authenticated owner's frozen run manifest."""

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types


class ApprovedKnowledgeMemory(BaseMemoryService):
    def __init__(self, owner_id, manifest):
        self.owner_id, self.manifest = owner_id, manifest

    async def add_session_to_memory(self, session):
        raise PermissionError("Sessions cannot approve or ingest knowledge; use the review API")

    async def search_memory(self, *, app_name, user_id, query):
        if user_id != self.owner_id:
            raise PermissionError("Memory owner does not match this run")
        # Retrieval was already scoped and frozen before any worker started.
        return SearchMemoryResponse(
            memories=[
                MemoryEntry(author="approved_knowledge", content=types.Content(role="user", parts=[types.Part(text=item.text)]))
                for item in self.manifest.memories
            ]
        )
