import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    'DATABASE_URL', 'postgresql+psycopg://codeflow:codeflow@localhost:5433/codeflow'
)
SESSION_SECRET = os.environ.get('SESSION_SECRET', 'codeflow-secret-2024')

ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.environ.get('ADMIN_EMAILS', '').split(',')
    if email.strip()
}

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')

MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID', '')
MICROSOFT_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET', '')
MICROSOFT_TENANT_ID = os.environ.get('MICROSOFT_TENANT_ID', 'common')

GOOGLE_SSO_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
MICROSOFT_SSO_ENABLED = bool(MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET)

# LLM provider — powers AI Question Authoring, assignment source/test-suite drafting,
# and practice-answer feedback. Three providers are supported; whichever is configured
# first in this priority order wins: Groq, then Gemini, then OpenRouter. Leave all
# blank to disable AI features entirely (pages show a setup notice instead of erroring).
#
# Groq — get a key at https://console.groq.com/keys. Free tier, no billing card
# required to start. OpenAI-compatible API, same request/response shape as OpenRouter.
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')
GROQ_ENABLED = bool(GROQ_API_KEY)

# Gemini (Google AI Studio) — get a key at https://aistudio.google.com/apikey.
# A direct API key tied to your Google account, but Google requires billing linked
# to the underlying Cloud project before granting any quota, even free-tier.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash')
GEMINI_ENABLED = bool(GEMINI_API_KEY)

# OpenRouter — get a key at https://openrouter.ai/keys.
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'google/gemini-2.0-flash-001')
OPENROUTER_ENABLED = bool(OPENROUTER_API_KEY)

AI_ENABLED = GROQ_ENABLED or GEMINI_ENABLED or OPENROUTER_ENABLED
