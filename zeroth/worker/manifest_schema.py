"""Schema for the deployment manifest the model produces.

Validation happens locally, before any Zerops project is provisioned. A bad
manifest costs milliseconds here instead of a three-minute provisioning cycle
— this is failure class A.
"""

# Service types Zerops understands. Anything outside this set is a
# hallucination and is rejected before it reaches the platform.
ALLOWED_TYPES = {
    "nodejs@22", "nodejs@20",
    "python@3.12", "python@3.11",
    "go@1.22",
    "php-apache@8.3",
    "static",
    "postgresql@17", "postgresql@16",
    "mariadb@10.6",
    "valkey@7.2",
    "keydb@6",
    "objectstorage",
}

RUNTIME_TYPES = {t for t in ALLOWED_TYPES if t.split("@")[0] in
                 {"nodejs", "python", "go", "php-apache"}}

MANIFEST_SCHEMA = {
    "type": "object",
    "required": ["project_name", "services"],
    "properties": {
        "project_name": {"type": "string", "minLength": 3, "maxLength": 60},
        "summary": {"type": "string"},
        "services": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["hostname", "type", "role"],
                "properties": {
                    "hostname": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]{1,24}$",
                    },
                    "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                    "role": {
                        "type": "string",
                        "enum": ["api", "web", "worker", "database", "cache", "storage"],
                    },
                    "reason": {"type": "string"},
                    "public": {"type": "boolean"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "build_commands": {"type": "array", "items": {"type": "string"}},
                    "start_command": {"type": "string"},
                    "env": {"type": "object"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}
