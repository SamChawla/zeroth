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
from dataclasses import dataclass, field


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
