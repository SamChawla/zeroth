"""Known-good Zerops configuration, per stack.

Zeroth used to ask a model to write zerops.yaml from an empty template, then
rediscover the platform's rules one failed deployment at a time. Those rules are
published: zeropsio/recipe-* ships working configuration per stack, and the
platform documents its hard constraints. Everything here is copied from that
material, not inferred - each entry cites where it came from, so a wrong value
can be checked against its source rather than argued about.

The model still decides WHAT to deploy (services, roles, which framework). This
module decides HOW that stack is built and started on Zerops, because that part
is not a judgement call and should not be re-derived per run.

Vendored rather than fetched: coverage of the recipe repositories is uneven
(nodejs, python and django exist under those names; nextjs does not), and a
generator that silently degrades when a URL 404s is worse than one that is
explicit about what it knows.
"""
import json
from dataclasses import dataclass, field

import httpx


@dataclass
class Recipe:
    """The build and run shape for one stack."""

    os: str = "ubuntu"
    build_commands: list[str] = field(default_factory=list)
    add_to_run_prepare: list[str] = field(default_factory=list)
    prepare_commands: list[str] = field(default_factory=list)
    init_commands: list[str] = field(default_factory=list)
    cache: list[str] = field(default_factory=list)
    build_env: dict = field(default_factory=dict)
    run_env: dict = field(default_factory=dict)
    default_port: int = 8000
    start: str = ""
    source: str = ""


# Base runtimes. Commands come from the published recipes; the os choice is
# ubuntu for anything that installs dependencies, because alpine is musl and
# packages without a musl wheel build from source and fail.
BASE = {
    "python": Recipe(
        os="ubuntu",
        build_commands=["pip install -r requirements.txt"],
        # Build and run are separate containers and prepareCommands execute
        # before deploy files land, so the requirements file has to be bridged
        # explicitly. This exact pair is what zeropsio/recipe-python ships.
        add_to_run_prepare=["requirements.txt"],
        prepare_commands=["python3 -m pip install --ignore-installed -r requirements.txt"],
        cache=["~/.cache/pip"],
        default_port=8000,
        source="zeropsio/recipe-python",
    ),
    "nodejs": Recipe(
        os="ubuntu",
        build_commands=["npm ci"],
        add_to_run_prepare=["package.json", "package-lock.json"],
        prepare_commands=["npm install --omit=dev"],
        cache=["node_modules"],
        default_port=3000,
        source="zeropsio/recipe-nodejs",
    ),
    "go": Recipe(
        os="ubuntu",
        build_commands=["go build -o app ."],
        cache=["/go/pkg/mod"],
        default_port=8080,
        start="./app",
        source="zeropsio/recipe-go",
    ),
    "php-apache": Recipe(
        os="ubuntu",
        build_commands=["composer install --no-dev --optimize-autoloader"],
        cache=["vendor"],
        default_port=80,
        source="zeropsio/recipe-php",
    ),
    "static": Recipe(os="alpine", default_port=8080, source="platform docs"),
}


# Framework overlays. These exist because a framework can need things the base
# runtime does not, and getting them wrong is what actually breaks deployments.
FRAMEWORKS = {
    "nextjs": Recipe(
        os="ubuntu",
        build_commands=["npm ci", "npm run build"],
        add_to_run_prepare=["package.json", "package-lock.json"],
        prepare_commands=[],
        # Standalone tracing does NOT copy static assets or public/ next to
        # server.js, so the server 404s them unless they are put there. From the
        # nextjs-ssr-hello-world recipe.
        init_commands=[
            "mkdir -p .next/standalone/.next",
            "cp -rT .next/static .next/standalone/.next/static || true",
            "cp -rT public .next/standalone/public || true",
        ],
        # Only node_modules. Caching .next/cache makes Zerops restore ownership
        # that trips EACCES on the following build.
        cache=["node_modules"],
        build_env={"NODE_OPTIONS": "--max-old-space-size=1536"},
        default_port=3000,
        # Next.js standalone binds localhost unless HOSTNAME says otherwise, and
        # the L7 balancer cannot reach localhost - that is a 502 with a healthy
        # looking container. HOSTNAME is reserved, so it cannot be set through
        # envVariables and has to be inlined on the start command.
        start="env HOSTNAME=0.0.0.0 node .next/standalone/server.js",
        source="zerops nextjs-ssr-hello-world recipe",
    ),
    "django": Recipe(
        os="ubuntu",
        build_commands=["pip install -r requirements.txt", "python manage.py collectstatic --noinput"],
        add_to_run_prepare=["requirements.txt"],
        prepare_commands=["python3 -m pip install --ignore-installed -r requirements.txt"],
        cache=["~/.cache/pip"],
        default_port=8000,
        start="gunicorn --bind 0.0.0.0:8000 --workers 2 config.wsgi",
        source="zeropsio/recipe-django",
    ),
    "flask": Recipe(
        os="ubuntu",
        build_commands=["pip install -r requirements.txt"],
        add_to_run_prepare=["requirements.txt"],
        prepare_commands=["python3 -m pip install --ignore-installed -r requirements.txt"],
        cache=["~/.cache/pip"],
        default_port=8000,
        start="gunicorn --bind 0.0.0.0:8000 app:app",
        source="zeropsio/recipe-python",
    ),
    "fastapi": Recipe(
        os="ubuntu",
        build_commands=["pip install -r requirements.txt"],
        add_to_run_prepare=["requirements.txt"],
        prepare_commands=["python3 -m pip install --ignore-installed -r requirements.txt"],
        cache=["~/.cache/pip"],
        default_port=8000,
        start="uvicorn main:app --host 0.0.0.0 --port 8000",
        source="zeropsio/recipe-python",
    ),
    "streamlit": Recipe(
        os="ubuntu",
        build_commands=["pip install -r requirements.txt"],
        add_to_run_prepare=["requirements.txt"],
        prepare_commands=["python3 -m pip install --ignore-installed -r requirements.txt"],
        cache=["~/.cache/pip"],
        default_port=8501,
        start="streamlit run app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true",
        source="zeropsio/recipe-python + streamlit docs",
    ),
    "express": Recipe(
        os="ubuntu",
        build_commands=["npm ci"],
        add_to_run_prepare=["package.json", "package-lock.json"],
        prepare_commands=["npm install --omit=dev"],
        cache=["node_modules"],
        default_port=3000,
        start="node index.js",
        source="zeropsio/recipe-nodejs",
    ),
}


def for_service(service_type: str, framework: str = "") -> Recipe:
    """The recipe for a service: framework overlay if known, else the runtime.

    The model's start_command still wins when it has one, because it saw the
    repository and this table did not. Everything else comes from here.
    """
    key = (framework or "").strip().lower().replace(".", "").replace(" ", "")
    if key in FRAMEWORKS:
        return FRAMEWORKS[key]
    base = (service_type or "").split("@")[0]
    return BASE.get(base, BASE["python"])

# ---------------------------------------------------------------------------
# The official corpus.
#
# zeropsio publishes ~87 recipe repositories, each a working application with
# its own zerops.yml. That is the authoritative starting point for a stack and
# it is maintained by the platform, so it beats anything inferred here.
#
# It is a starting point rather than a law: the recipes are configurations for
# their own demo applications and they disagree with each other (recipe-flask
# builds on alpine, recipe-python does not; recipe-nextjs-nodejs runs
# `next start` where a standalone build needs a different launch entirely).
# So a fetched recipe is still put through constraints.check before use - the
# recipe proposes, the constraints decide.
RECIPE_REPOS = {
    # framework -> repository
    "nextjs": "recipe-nextjs-nodejs",
    "nuxt": "recipe-nuxt-nodejs",
    "astro": "recipe-astro-nodejs",
    "remix": "recipe-remix-nodejs",
    "qwik": "recipe-qwik-nodejs",
    "analog": "recipe-analog-nodejs",
    "nitro": "recipe-nitro-nodejs",
    "react": "recipe-react-nodejs",
    "angular": "recipe-angular-static",
    "nestjs": "recipe-nestjs",
    "adonis": "recipe-adonis",
    "express": "recipe-nodejs",
    "django": "recipe-django",
    "flask": "recipe-flask",
    "laravel": "recipe-laravel-minimal",
    "filament": "recipe-filament",
    "nette": "recipe-nette",
    "rails": "recipe-rails",
    "phoenix": "recipe-phoenix",
    "echo": "recipe-echo",
    "medusa": "recipe-medusa",
    "payload": "recipe-payload",
    "sails": "recipe-sails",
    "redwoodjs": "recipe-redwoodjs",
    "elysia": "recipe-elysia",
    "hono": "recipe-hono-deno",
}

RECIPE_REPOS_BY_LANGUAGE = {
    "python": "recipe-python",
    "javascript": "recipe-nodejs",
    "typescript": "recipe-nodejs",
    "nodejs": "recipe-nodejs",
    "node": "recipe-nodejs",
    "go": "recipe-go",
    "golang": "recipe-go",
    "php": "recipe-php",
    "ruby": "recipe-ruby",
    "rust": "recipe-rust",
    "java": "recipe-java",
    "elixir": "recipe-elixir",
    "deno": "recipe-deno",
    "bun": "recipe-bun",
}

RAW = "https://raw.githubusercontent.com/zeropsio/{repo}/main/{path}"
_CACHE_TTL = 86400


def repo_for(language: str, framework: str = "") -> str:
    """Which official recipe repository covers this stack, if any."""
    key = (framework or "").strip().lower().replace(".", "").replace(" ", "")
    if key in RECIPE_REPOS:
        return RECIPE_REPOS[key]
    return RECIPE_REPOS_BY_LANGUAGE.get((language or "").strip().lower(), "")


def fetch_official(language: str, framework: str = "") -> dict:
    """The official recipe's own configuration, cached.

    Returns {} when there is no recipe for the stack or the network is
    unavailable - callers fall back to the vendored tables above, which is why
    those still exist. Never raises: a missing recipe must not fail a run.
    """
    repo = repo_for(language, framework)
    if not repo:
        return {}

    from zeroth import bus  # local import: keeps this module importable offline

    key = f"zeroth:recipe:{repo}"
    try:
        cached = bus.client().get(key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 - a cache miss is not an error
        pass

    found = {"repo": repo, "url": f"https://github.com/zeropsio/{repo}"}
    for name, path in (("zerops_yml", "zerops.yml"), ("import_yaml", "import.yaml")):
        try:
            resp = httpx.get(RAW.format(repo=repo, path=path), timeout=8, follow_redirects=True)
            if resp.status_code == 200 and resp.text.strip():
                found[name] = resp.text
        except httpx.HTTPError:
            continue
    if "zerops_yml" not in found:
        return {}

    try:
        bus.client().set(key, json.dumps(found), ex=_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return found
