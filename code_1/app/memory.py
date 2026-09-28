import uuid
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .models import MemoryFact
from .security import redact_pii


class HindsightMemory:
    """Hindsight holds semantic/temporal memories; PostgreSQL holds auditable facts."""

    def __init__(self) -> None:
        self.base = settings.hindsight_url.rstrip("/")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.hindsight_api_key}"} if settings.hindsight_api_key else {}

    @staticmethod
    def bank_id(customer_id: uuid.UUID) -> str:
        return f"support-{customer_id.hex}"

    async def retain(self, customer_id: uuid.UUID, content: str, ticket_id: uuid.UUID | None = None) -> None:
        bank = self.bank_id(customer_id)
        safe = redact_pii(content)
        async with httpx.AsyncClient(timeout=30) as client:
            # Bank creation is idempotent in Hindsight; PUT also applies mission config.
            await client.put(f"{self.base}/v1/default/banks/{bank}", headers=self.headers(), json={
                "retain_mission": "Remember customer environment, support issue history, successful and failed fixes, and customer experience. Exclude unnecessary personal data.",
            })
            response = await client.post(f"{self.base}/v1/default/banks/{bank}/memories", headers=self.headers(), json={
                "items": [{"content": safe, "context": "customer support conversation", "document_id": f"ticket-{ticket_id or uuid.uuid4()}", "update_mode": "append", "tags": [f"user:{customer_id.hex}"]}],
                "async": False,
            })
            response.raise_for_status()

    async def recall(self, customer_id: uuid.UUID, query: str) -> str:
        bank = self.bank_id(customer_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base}/v1/default/banks/{bank}/memories/recall", headers=self.headers(), json={
                "query": redact_pii(query), "tags": [f"user:{customer_id.hex}"], "max_tokens": 1600,
            })
            response.raise_for_status()
            return str(response.json())[:12000]


async def retrieve_pg_facts(session: AsyncSession, customer_id: uuid.UUID, query: str, embedding: list[float]) -> list[str]:
    stmt = select(MemoryFact).where(MemoryFact.customer_id == customer_id).order_by(MemoryFact.embedding.cosine_distance(embedding)).limit(8)
    facts = (await session.scalars(stmt)).all()
    return [f"[{fact.category}] {fact.content}" for fact in facts]
