# Configuration Boundary

## Rule

- `.env` = secrets and infrastructure only.
- UI/DB = bot behavior configuration.

## Keep In `.env`

- `DEEPGRAM_API_KEY`
- `CEREBRAS_API_KEY`
- `CARTESIA_API_KEY`
- `MONGO_URI`
- `MONGO_DB_NAME`
- `EXOTEL_ACCOUNT_SID`
- `EXOTEL_API_KEY`
- `EXOTEL_API_TOKEN`
- `EXOTEL_VIRTUAL_NUMBER`
- `EXOTEL_FLOW_ID`
- `EXOTEL_SUBDOMAIN`
- `PUBLIC_NGROK_URL`

## Move Later To UI/DB

- system prompt
- scenario instructions
- greeting prompt
- fallback text
- LLM model
- LLM temperature
- max tokens
- TTS voice
- TTS model
- turn detection profile
- flow/use-case type
- slot schema
- closing behavior

This is documentation-only guidance; implementation changes are intentionally deferred.