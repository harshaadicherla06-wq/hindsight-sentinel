import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from .agent import memory, respond, client
from .config import settings
from .database import Base, engine, get_session
from .models import Customer, Message, MemoryFact, Ticket
from .security import encrypt, redact_pii


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Memory First Support Agent", version="1.0.0", lifespan=lifespan)


class StartRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    plan: str | None = None
    device: str | None = None
    app_version: str | None = None


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)


class ResolveRequest(BaseModel):
    resolution: str = Field(min_length=1, max_length=5000)
    worked: bool = True


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/conversations")
async def start_conversation(body: StartRequest, session: AsyncSession = Depends(get_session)):
    email = str(body.email).lower()
    customer = await session.scalar(select(Customer).where(Customer.email == email))
    if customer is None:
        customer = Customer(email=email, name=body.name, plan=body.plan, device=body.device, app_version=body.app_version)
        session.add(customer)
        await session.flush()
    else:
        for key in ("name", "plan", "device", "app_version"):
            value = getattr(body, key)
            if value:
                setattr(customer, key, value)
    ticket = Ticket(customer_id=customer.id, issue="Conversation started")
    session.add(ticket)
    await session.commit()
    return {"customer_id": str(customer.id), "ticket_id": str(ticket.id), "message": "Customer history loaded. Tell us what you need help with."}


@app.post("/conversations/{ticket_id}/messages")
async def send_message(ticket_id: uuid.UUID, body: MessageRequest, session: AsyncSession = Depends(get_session)):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Conversation not found")
    customer = await session.get(Customer, ticket.customer_id)
    raw = body.message.strip()
    safe = redact_pii(raw)
    old_score = ticket.frustration
    msg = Message(ticket_id=ticket.id, role="user", body=encrypt(safe))
    session.add(msg)
    if ticket.issue == "Conversation started":
        ticket.issue = safe[:1000]
    else:
        ticket.issue = (ticket.issue + "\n" + safe)[:5000]
    try:
        result = await client.chat.completions.create(model=settings.openai_model, messages=[{"role": "system", "content": "Return only a frustration score as a decimal from 0 to 1, based on the latest customer message."}, {"role": "user", "content": safe}], temperature=0)
        score = float(result.choices[0].message.content or 0)
        ticket.frustration = max(old_score, min(1.0, score))
    except Exception:
        ticket.frustration = old_score
    await session.flush()
    answer, escalated = await respond(session, customer, ticket, safe)
    session.add(Message(ticket_id=ticket.id, role="assistant", body=encrypt(redact_pii(answer))))
    ticket.status = "escalated" if escalated or ticket.frustration >= settings.frustration_escalation_threshold else "open"
    await session.commit()
    return {"ticket_id": str(ticket.id), "reply": answer, "frustration": ticket.frustration, "escalated": ticket.status == "escalated"}


@app.post("/conversations/{ticket_id}/resolve")
async def resolve(ticket_id: uuid.UUID, body: ResolveRequest, session: AsyncSession = Depends(get_session)):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Conversation not found")
    customer = await session.get(Customer, ticket.customer_id)
    clean = redact_pii(body.resolution)
    ticket.resolution = clean
    ticket.status = "resolved" if body.worked else "unresolved"
    embedding = (await client.embeddings.create(model=settings.openai_embedding_model, input=clean)).data[0].embedding
    session.add(MemoryFact(customer_id=customer.id, category="successful_fix" if body.worked else "failed_fix", content=clean, embedding=embedding, source_ticket_id=ticket.id))
    profile = ", ".join(f"{k}={v}" for k, v in {"plan": customer.plan, "device": customer.device, "app_version": customer.app_version}.items() if v)
    fact_text = f"Customer support ticket {ticket.id}. Issue: {ticket.issue}. Resolution ({'worked' if body.worked else 'failed'}): {clean}. Environment: {profile}. Frustration score: {ticket.frustration}."
    try:
        await memory.retain(customer.id, fact_text, ticket.id)
    except Exception as exc:
        # Keep the durable PostgreSQL record even if Hindsight is temporarily offline.
        pass
    await session.commit()
    return {"ticket_id": str(ticket.id), "status": ticket.status, "memory_saved": True}
