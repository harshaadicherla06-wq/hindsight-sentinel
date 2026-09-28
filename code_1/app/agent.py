import json
import uuid
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .memory import HindsightMemory, retrieve_pg_facts
from .models import Customer, Message, MemoryFact, Ticket
from .security import redact_pii

client = AsyncOpenAI(api_key=settings.openai_api_key)
memory = HindsightMemory()

TOOLS = [{"type": "function", "function": {"name": "search_customer_memory", "description": "Search this customer's past support history, environment, and prior fixes. Use before proposing troubleshooting steps.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}}, {"type": "function", "function": {"name": "escalate_to_human", "description": "Prepare a human escalation when the issue recurs, the customer is highly frustrated, or the agent cannot safely resolve it.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"], "additionalProperties": False}}}]


async def respond(session: AsyncSession, customer: Customer, ticket: Ticket, user_text: str) -> tuple[str, bool]:
    past = (await session.scalars(select(Ticket).where(Ticket.customer_id == customer.id, Ticket.id != ticket.id).order_by(Ticket.created_at.desc()).limit(8))).all()
    recent = [f"Issue: {t.issue}; status: {t.status}; resolution: {t.resolution or 'none'}" for t in past]
    try:
        h_mem = await memory.recall(customer.id, user_text)
    except Exception:
        h_mem = "Hindsight recall unavailable. Use PostgreSQL history only."
    try:
        embedding = (await client.embeddings.create(model=settings.openai_embedding_model, input=redact_pii(user_text))).data[0].embedding
        pg_facts = await retrieve_pg_facts(session, customer.id, user_text, embedding)
    except Exception:
        pg_facts = []
    facts = await session.scalars(select(MemoryFact).where(MemoryFact.customer_id == customer.id).limit(20))
    known = [f"[{f.category}] {f.content}" for f in facts]
    profile = {"name": customer.name, "plan": customer.plan, "device": customer.device, "app_version": customer.app_version}
    msgs = [{"role": "system", "content": "You are a memory-first customer support agent. Start with what is already known; do not ask customers to repeat known details. Use memory search before troubleshooting and never recommend a fix recorded as failed. Ground claims in retrieved history or clearly label uncertainty. Be calm, empathetic, concise. If frustration is high, issue recurs, or risk is significant, call escalate_to_human. Do not reveal private internal notes. Assess frustration from 0 (calm) to 1 (very upset)."}, {"role": "user", "content": f"Customer profile: {json.dumps(profile)}\nKnown facts: {known}\nSimilar facts: {pg_facts}\nHindsight memories: {h_mem}\nRecent tickets: {recent}\nCustomer frustration score: {ticket.frustration:.2f}\nCurrent issue: {redact_pii(user_text)}"}]
    escalated = False
    for _ in range(4):
        result = await client.chat.completions.create(model=settings.openai_model, messages=msgs, tools=TOOLS, tool_choice="auto", temperature=0.2)
        answer = result.choices[0].message
        msgs.append(answer.model_dump(exclude_none=True))
        if not answer.tool_calls:
            return answer.content or "I’m sorry, I couldn’t produce a response. I’ll connect you with a support specialist.", escalated
        for call in answer.tool_calls:
            args = json.loads(call.function.arguments)
            if call.function.name == "search_customer_memory":
                try:
                    result_text = await memory.recall(customer.id, args["query"])
                except Exception:
                    result_text = "Memory service unavailable."
            else:
                escalated = True
                result_text = f"Human escalation prepared. Reason: {redact_pii(args['reason'])}. Summary: {redact_pii(user_text)}. Prior tickets: {recent}"
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": result_text})
    return "I’m bringing in a support specialist with the history of this issue so you won’t need to start over.", True
