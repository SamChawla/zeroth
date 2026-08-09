from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://zeroth:zeroth@localhost:5432/zeroth"
    valkey_url: str = "redis://localhost:6379/0"

    euri_api_key: str = ""
    euri_base_url: str = "https://api.euron.one/api/v1/euri"
    euri_model: str = "gpt-4.1-mini"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Ordered fallback chain, tried in sequence — each model has its own
    # max-completion-tokens ceiling (see llm.GROQ_MODEL_LIMITS), so a request
    # too large for one can still succeed against the next.
    groq_models: str = "llama-3.3-70b-versatile,openai/gpt-oss-120b,llama-3.1-8b-instant"

    # Not ZEROPS_-prefixed: Zerops rejects custom env vars with that prefix
    # on its own services (confirmed against a real project-import — see
    # ZcliProvider's docstring).
    zcli_token: str = ""
    pathfinder_provider: str = "simulated"  # simulated | zcli
    zeroth_public_url: str = "http://localhost:8000"

    # Interactive API docs stay off in a deployed instance: the surface they
    # publish includes the token-accepting verify endpoint.
    expose_api_docs: bool = False

    # Per-run LLM token ceiling when running on the house keys. BYOK runs
    # are exempt: the key's owner decides when it is exhausted.
    llm_token_budget: int = 60_000

    # Optional. The size preflight calls api.github.com, which allows 60
    # unauthenticated requests an hour - exhausted in one busy afternoon,
    # after which oversized repos slip through to the clone guard. A token
    # raises that to 5,000.
    github_token: str = ""

    max_repo_mb: int = 50
    # Verification is opt-in and someone is watching it happen, so it is tuned
    # for a bounded wait rather than maximum persistence: two attempts still
    # demonstrates the repair loop, three mostly demonstrates patience.
    max_attempts: int = 2
    # Import of an ephemeral project. Usually under a minute, but the
    # platform queues under load and a timeout here kills a run before it
    # deployed anything - observed live when parallel runs slowed core
    # activation past two minutes.
    provision_timeout_s: int = 300
    # A real application build - a Next.js production build, say - does not fit
    # in four minutes on a small builder.
    deploy_timeout_s: int = 600
    max_concurrent_runs: int = 2
    rate_limit_per_hour: int = 10

    queue_key: str = "zeroth:jobs"
    events_channel_prefix: str = "zeroth:events:"


settings = Settings()
