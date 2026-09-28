# Memory First Support Agent

Runnable FastAPI backend for persistent customer support memory. It uses Hindsight for semantic and temporal memory, PostgreSQL/pgvector for structured customer records and similarity search, and OpenAI tool calling for the support agent.

## Start locally

1. Install Docker and Python 3.12 or newer.
2. Copy `.env.example` to `.env`, then set `OPENAI_API_KEY`.
3. Set `FERNET_KEY` to a newly generated Fernet key. Generate one with:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. From this directory, run:

   ```powershell
   docker compose up --build
   ```

   The API is at `http://localhost:8000`; interactive API docs are at `/docs`. PostgreSQL and Hindsight persist data in Docker volumes. The Hindsight container uses its bundled embedded PostgreSQL; the app's own PostgreSQL instance uses pgvector.

## API flow

Create a customer conversation:

```http
POST /conversations
Content-Type: application/json

{"email":"customer@example.com","name":"Alex","plan":"Pro","device":"Windows laptop","app_version":"4.2.1"}
```

Send the returned `ticket_id` a message:

```http
POST /conversations/{ticket_id}/messages
Content-Type: application/json

{"message":"Sync is still failing after I reinstalled the app."}
```

Close out the interaction with its outcome so future agents avoid repeating failed fixes:

```http
POST /conversations/{ticket_id}/resolve
Content-Type: application/json

{"resolution":"Reset the local sync cache; sync completed successfully.","worked":true}
```

The agent retrieves previous tickets and memories before responding, keeps a per-customer Hindsight bank, embeds durable resolution facts for pgvector search, and prepares a human handoff when frustration is high or escalation is warranted. Messages and memory content are PII-redacted; message bodies are encrypted at rest when `FERNET_KEY` is set. Keep that key stable and store it securely: losing it makes encrypted message bodies unreadable.

## Configuration

Set `HINDSIGHT_URL` to your Hindsight Cloud API or a self-hosted endpoint. For Hindsight Cloud, add its API token in `HINDSIGHT_API_KEY`. The local Docker service exposes the API on port 8888 and its UI on port 9999. Set `FRUSTRATION_ESCALATION_THRESHOLD` to tune auto-escalation (0–1).

The pgvector column currently uses 1536 dimensions, matching `text-embedding-3-small`. If you change embedding models, update the vector dimension in `app/models.py` and recreate/migrate the table.

## Notes

- This is a backend starter, not a complete deployment. Add authentication/authorization, rate limiting, schema migrations, operational monitoring, and an approved human-agent delivery channel before production use.
- The email is used to identify a customer; restrict API access and avoid exposing customer records to untrusted callers.
- PostgreSQL is the source of truth for profiles, tickets, messages, and searchable facts. Hindsight provides complementary extracted memory and retrieval.
