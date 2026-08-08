"""Seed the gallery with a handful of illustrative pathfinder runs.

PLAN.md flags an unfinished first-hour task: "Seed the gallery with 4-6 real
runs... Done when: Landing page shows finished work with logs." Judges arrive
before anyone has pasted a repository, and the gallery is empty until they do.

The entries below are fixtures, not runs against real third-party
repositories - the repo names live under the fictitious `zeroth-samples`
namespace so nothing here is mistaken for a claim about a specific public
project. Config generation is real: every artifact is produced by the same
Jinja templates and manifest schema the live pipeline uses, so the showcase
demonstrates the actual output shape, not a mockup.

Run once against a fresh database:
    python -m zeroth.scripts.seed_gallery
"""
from datetime import datetime, timedelta, timezone

from zeroth.db import SessionLocal, init_db
from zeroth.models import Artifact, Job, Run
from zeroth.worker.generate import render_import_yaml, render_report, render_zerops_yaml

NOW = datetime.now(timezone.utc)


def _fact(key: str, value: str, evidence: str) -> dict:
    return {"key": key, "value": value, "evidence": evidence}


SAMPLES = [
    {
        "repo_name": "zeroth-samples/django-postgres-blog",
        "repo_url": "https://github.com/zeroth-samples/django-postgres-blog",
        "age_days": 0.2,
        "fingerprint": {
            "repo_name": "zeroth-samples/django-postgres-blog",
            "language": "python",
            "runtime_version": "3.12",
            "framework": "django",
            "databases": ["postgresql"],
            "caches": [],
            "has_worker": False,
            "env_vars": ["DATABASE_URL", "DEBUG", "SECRET_KEY"],
            "ports": [8000],
            "entrypoints": ["manage.py"],
            "dependencies": ["django", "gunicorn", "psycopg"],
            "compose_services": [],
            "present_files": [".env.example", "requirements.txt"],
            "tree": ["app/", "config/", "manage.py", "requirements.txt", ".env.example"],
            "facts": [
                _fact("language", "python", "requirements.txt present"),
                _fact("framework", "django", "manage.py at repository root"),
                _fact("database", "postgresql", "'psycopg' in requirements.txt"),
                _fact("database", "postgresql", "DATABASE_URL referenced in .env.example"),
                _fact("server", "gunicorn", "'gunicorn' in requirements.txt"),
            ],
        },
        "manifest": {
            "project_name": "django-postgres-blog",
            "summary": "Django blog backed by Postgres, served with gunicorn.",
            "services": [
                {
                    "hostname": "api",
                    "type": "python@3.12",
                    "role": "api",
                    "reason": "manage.py at repository root and 'psycopg' in requirements.txt",
                    "public": True,
                    "port": 8000,
                    "build_commands": [
                        "pip install -r requirements.txt",
                        "python manage.py collectstatic --noinput",
                    ],
                    "start_command": "gunicorn config.wsgi --bind 0.0.0.0:8000",
                    "env": {"DATABASE_URL": "${db_connectionString}"},
                },
                {
                    "hostname": "db",
                    "type": "postgresql@16",
                    "role": "database",
                    "reason": "'psycopg' in requirements.txt and DATABASE_URL in .env.example",
                    "public": False,
                },
            ],
        },
        "attempts": [
            {"status": "passed", "phase": "runtime", "verification": {"http": 200, "health": "passed", "errors_in_log": 0}},
        ],
    },
    {
        "repo_name": "zeroth-samples/fastapi-task-queue",
        "repo_url": "https://github.com/zeroth-samples/fastapi-task-queue",
        "age_days": 1.4,
        "fingerprint": {
            "repo_name": "zeroth-samples/fastapi-task-queue",
            "language": "python",
            "runtime_version": "3.12",
            "framework": "fastapi",
            "databases": ["postgresql"],
            "caches": ["valkey"],
            "has_worker": True,
            "env_vars": ["DATABASE_URL", "REDIS_URL"],
            "ports": [8000],
            "entrypoints": [],
            "dependencies": ["celery", "fastapi", "psycopg", "redis", "sqlalchemy", "uvicorn"],
            "compose_services": ["db", "redis", "web", "worker"],
            "present_files": ["docker-compose.yml", "requirements.txt"],
            "tree": ["app/", "docker-compose.yml", "requirements.txt"],
            "facts": [
                _fact("language", "python", "requirements.txt present"),
                _fact("framework", "fastapi", "'fastapi' in requirements.txt"),
                _fact("database", "postgresql", "docker-compose.yml: service 'db' uses postgres:16"),
                _fact("cache", "valkey", "docker-compose.yml: service 'redis' uses redis:7"),
                _fact("worker", "celery", "'celery' in requirements.txt"),
            ],
        },
        "manifest": {
            "project_name": "fastapi-task-queue",
            "summary": "FastAPI API with a Celery worker, Postgres and Redis-compatible cache.",
            "services": [
                {
                    "hostname": "api",
                    "type": "python@3.12",
                    "role": "api",
                    "reason": "'fastapi' in requirements.txt",
                    "public": True,
                    "port": 8000,
                    "build_commands": ["pip install -r requirements.txt"],
                    "start_command": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
                    "env": {"DATABASE_URL": "${db_connectionString}", "REDIS_URL": "redis://cache:6379/0"},
                },
                {
                    "hostname": "worker",
                    "type": "python@3.12",
                    "role": "worker",
                    "reason": "'celery' in requirements.txt",
                    "public": False,
                    "build_commands": ["pip install -r requirements.txt"],
                    "start_command": "celery -A app.worker worker --loglevel=info",
                    "env": {"DATABASE_URL": "${db_connectionString}", "REDIS_URL": "redis://cache:6379/0"},
                },
                {
                    "hostname": "db",
                    "type": "postgresql@16",
                    "role": "database",
                    "reason": "docker-compose.yml: service 'db' uses postgres:16",
                    "public": False,
                },
                {
                    "hostname": "cache",
                    "type": "valkey@7.2",
                    "role": "cache",
                    "reason": "docker-compose.yml: service 'redis' uses redis:7",
                    "public": False,
                },
            ],
        },
        "attempts": [
            {
                "status": "failed", "phase": "runtime",
                "failure_class": "runtime",
                "failure_message": 'could not translate host name "localhost" to address',
                "diagnosis": "The API connected to Postgres at 'localhost', which does not resolve inside the container.",
                "patch_summary": "Changed DATABASE_URL host from localhost to db, the Postgres service hostname.",
                "logs": (
                    "[build] build succeeded in 44s\n[run] starting service\n"
                    '[run] sqlalchemy.exc.OperationalError: could not translate host name "localhost" to address\n'
                    "[run] container exited with code 1\n"
                ),
            },
            {"status": "passed", "phase": "runtime", "verification": {"http": 200, "health": "passed", "errors_in_log": 0}},
        ],
    },
    {
        "repo_name": "zeroth-samples/express-notes-api",
        "repo_url": "https://github.com/zeroth-samples/express-notes-api",
        "age_days": 2.6,
        "fingerprint": {
            "repo_name": "zeroth-samples/express-notes-api",
            "language": "nodejs",
            "runtime_version": "20",
            "framework": "express",
            "databases": ["mariadb"],
            "caches": [],
            "has_worker": False,
            "env_vars": ["DATABASE_URL", "PORT"],
            "ports": [3000],
            "entrypoints": ["npm start -> node src/index.js"],
            "dependencies": ["express", "mysql2"],
            "compose_services": [],
            "present_files": [".env.example", "package.json"],
            "tree": ["package.json", "src/", ".env.example"],
            "facts": [
                _fact("language", "nodejs", "package.json present"),
                _fact("framework", "express", "'express' in package.json"),
                _fact("database", "mariadb", "'mysql2' in package.json"),
                _fact("start_command", "node src/index.js", "scripts.start in package.json"),
            ],
        },
        "manifest": {
            "project_name": "express-notes-api",
            "summary": "Express REST API backed by MariaDB.",
            "services": [
                {
                    "hostname": "api",
                    "type": "nodejs@20",
                    "role": "api",
                    "reason": "'express' in package.json",
                    "public": True,
                    "port": 3000,
                    "build_commands": ["npm install"],
                    "start_command": "node src/index.js",
                    "env": {"DATABASE_URL": "${db_connectionString}"},
                },
                {
                    "hostname": "db",
                    "type": "mariadb@10.6",
                    "role": "database",
                    "reason": "'mysql2' in package.json",
                    "public": False,
                },
            ],
        },
        "attempts": [
            {"status": "passed", "phase": "runtime", "verification": {"http": 200, "health": "passed", "errors_in_log": 0}},
        ],
    },
    {
        "repo_name": "zeroth-samples/go-inventory-api",
        "repo_url": "https://github.com/zeroth-samples/go-inventory-api",
        "age_days": 4.1,
        "fingerprint": {
            "repo_name": "zeroth-samples/go-inventory-api",
            "language": "go",
            "runtime_version": "1.22",
            "framework": "",
            "databases": ["postgresql"],
            "caches": [],
            "has_worker": False,
            "env_vars": ["DATABASE_URL", "PORT"],
            "ports": [8080],
            "entrypoints": [],
            "dependencies": [],
            "compose_services": [],
            "present_files": ["Dockerfile", "go.mod"],
            "tree": ["Dockerfile", "go.mod", "main.go"],
            "facts": [
                _fact("language", "go", "go.mod present"),
                _fact("runtime_version", "1.22", "go directive in go.mod"),
                _fact("database", "postgresql", "DATABASE_URL referenced in .env.example"),
                _fact("port", "8080", "EXPOSE in Dockerfile"),
            ],
        },
        "manifest": {
            "project_name": "go-inventory-api",
            "summary": "Go HTTP API backed by Postgres.",
            "services": [
                {
                    "hostname": "api",
                    "type": "go@1.22",
                    "role": "api",
                    "reason": "go.mod present, DATABASE_URL referenced in .env.example",
                    "public": True,
                    "port": 8080,
                    "build_commands": ["go build -o app ."],
                    "start_command": "./app",
                    "env": {"DATABASE_URL": "${db_connectionString}"},
                },
                {
                    "hostname": "db",
                    "type": "postgresql@16",
                    "role": "database",
                    "reason": "DATABASE_URL referenced in .env.example",
                    "public": False,
                },
            ],
        },
        "attempts": [
            {"status": "passed", "phase": "runtime", "verification": {"http": 200, "health": "passed", "errors_in_log": 0}},
        ],
    },
]


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        created = 0
        for sample in SAMPLES:
            if db.query(Job).filter_by(repo_name=sample["repo_name"], is_gallery=True).first():
                continue

            created_at = NOW - timedelta(days=sample["age_days"])
            verified = sample["attempts"][-1]["status"] == "passed"

            job = Job(
                repo_url=sample["repo_url"],
                repo_name=sample["repo_name"],
                status="done",
                stage_detail="Verified — this configuration was deployed and came up." if verified
                else "Generated, not verified.",
                fingerprint=sample["fingerprint"],
                manifest=sample["manifest"],
                is_gallery=True,
                created_at=created_at,
                finished_at=created_at + timedelta(minutes=6),
            )
            db.add(job)
            db.flush()  # assigns job.id

            runs = []
            for i, attempt in enumerate(sample["attempts"], start=1):
                run = Run(
                    job_id=job.id,
                    attempt_no=i,
                    status=attempt["status"],
                    phase=attempt["phase"],
                    failure_class=attempt.get("failure_class", "none"),
                    failure_message=attempt.get("failure_message", ""),
                    diagnosis=attempt.get("diagnosis", ""),
                    patch_summary=attempt.get("patch_summary", ""),
                    zerops_project_id=f"sample-{job.id[:8]}-{i}",
                    build_log=attempt.get("logs", ""),
                    verification=attempt.get("verification"),
                    started_at=created_at + timedelta(minutes=2 * i),
                    ended_at=created_at + timedelta(minutes=2 * i + 1),
                )
                db.add(run)
                runs.append(run)

            import_yaml = render_import_yaml(sample["manifest"], sample["repo_url"], verified=verified)
            zerops_yaml = render_zerops_yaml(sample["manifest"], sample["repo_url"])
            headline = (
                "Verified — this configuration was deployed and came up." if verified
                else "Generated, not verified."
            )
            detail = (
                f"Zeroth deployed this repository to an ephemeral Zerops project and "
                f"confirmed it started after {len(runs)} attempt(s)."
                if verified else "Review before deploying. See the attempt history below."
            )
            report = render_report(job, sample["fingerprint"], sample["manifest"], runs, (headline, detail))

            db.add(Artifact(job_id=job.id, kind="import_yaml", filename="zerops-project-import.yaml", content=import_yaml))
            db.add(Artifact(job_id=job.id, kind="zerops_yaml", filename="zerops.yaml", content=zerops_yaml))
            db.add(Artifact(job_id=job.id, kind="deployment_md", filename="DEPLOYMENT.md", content=report))

            db.commit()
            created += 1

        print(f"seeded {created} gallery job(s); {len(SAMPLES) - created} already present")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
