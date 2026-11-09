"""Load config/models.yaml: the one place model names live.

Standard library only. Python 3.10 has no YAML or TOML reader, so this module
parses a small YAML subset (documented at the top of models.yaml):

* nested mappings, ``key: value``, indented with spaces (no tabs);
* ``#`` comments, whole-line or after a value (`` # ...``);
* scalars: plain or quoted text, integers, floats, ``true``/``false``,
  ``null``/``~``;
* one-line lists: ``[a, b, "c d"]``.

Anything else (block lists, anchors, multi-line strings) is rejected with the
line number rather than misread. The file stays valid YAML, so other tools
can read it too.

Set ``SOGICE_MODELS_CONFIG`` to use another file (e.g. in tests).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
ENV_VAR = "SOGICE_MODELS_CONFIG"
REQUIRED_ROLES = ("reading", "audit", "embedding")

_KEY_LINE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+):(?:[ ]+(?P<value>.*))?$")
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


class ConfigError(ValueError):
    """The models config is missing, malformed or incomplete."""


# ---------------------------------------------------------------------------
# YAML subset parser
# ---------------------------------------------------------------------------

def _strip_comment(text: str) -> str:
    """Remove a trailing ' # comment' that is outside quotes."""
    quote = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def _split_flow_list(body: str, lineno: int) -> list[str]:
    items, current, quote = [], [], None
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        elif char in "[]{}":
            raise ConfigError(f"line {lineno}: nested lists or mappings are not supported")
        else:
            current.append(char)
    if quote:
        raise ConfigError(f"line {lineno}: unclosed quote")
    last = "".join(current).strip()
    if last or items:
        items.append(last)
    if any(item == "" for item in items):
        raise ConfigError(f"line {lineno}: empty list item")
    return items


def _scalar(text: str, lineno: int) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text[:1] in "\"'":
        raise ConfigError(f"line {lineno}: unclosed quote")
    if text[:1] in "&*!|>{" or text.startswith("- "):
        raise ConfigError(f"line {lineno}: unsupported YAML feature {text[:1]!r}")
    if text.startswith("["):
        if not text.endswith("]"):
            raise ConfigError(f"line {lineno}: one-line lists must close on the same line")
        return [_scalar(item, lineno) for item in _split_flow_list(text[1:-1], lineno)]
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~", ""):
        return None
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


def parse_simple_yaml(text: str) -> dict:
    """Parse the YAML subset described in the module docstring."""
    root: dict = {}
    # Open mappings as (indent of their keys, mapping); root keys sit at 0.
    stack: list[tuple[int, dict]] = [(0, root)]
    pending: tuple[int, dict, str] | None = None  # "key:" waiting for a nested block

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"line {lineno}: indent with spaces, not tabs")
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if content.startswith("- "):
            raise ConfigError(f"line {lineno}: block lists are not supported; use [a, b]")
        match = _KEY_LINE.match(content)
        if not match:
            raise ConfigError(f"line {lineno}: expected 'key: value', got {content!r}")

        if pending is not None:
            parent_indent, parent, key = pending
            pending = None
            if indent > parent_indent:  # the block under "key:" starts here
                parent[key] = {}
                stack.append((indent, parent[key]))
        while indent < stack[-1][0]:
            stack.pop()
        if indent != stack[-1][0]:
            raise ConfigError(f"line {lineno}: inconsistent indentation")
        mapping = stack[-1][1]

        key, value = match.group("key"), match.group("value")
        if key in mapping:
            raise ConfigError(f"line {lineno}: duplicate key {key!r}")
        if value is None or value.strip() == "":
            mapping[key] = None  # stays None unless a nested block follows
            pending = (indent, mapping, key)
        else:
            mapping[key] = _scalar(value, lineno)
    return root


# ---------------------------------------------------------------------------
# Typed config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    description: str = ""
    temperature: float | None = None
    dimensions: int | None = None
    modalities: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModelsConfig:
    base_url: str
    timeout_seconds: float
    roles: dict
    path: Path

    def role(self, name: str) -> RoleConfig:
        try:
            return self.roles[name]
        except KeyError:
            raise ConfigError(f"no model role {name!r} in {self.path}") from None

    def model_for(self, name: str) -> str:
        return self.role(name).model


def _role(name: str, data: Any, path: Path) -> RoleConfig:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: roles.{name} must be a mapping")
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(f"{path}: roles.{name}.model must be a non-empty string")
    dimensions = data.get("dimensions")
    if dimensions is not None and (not isinstance(dimensions, int) or isinstance(dimensions, bool)
                                   or dimensions <= 0):
        raise ConfigError(f"{path}: roles.{name}.dimensions must be a positive integer")
    temperature = data.get("temperature")
    if temperature is not None and (isinstance(temperature, bool)
                                    or not isinstance(temperature, (int, float))):
        raise ConfigError(f"{path}: roles.{name}.temperature must be a number")
    modalities = data.get("modalities") or []
    if not isinstance(modalities, list) or not all(isinstance(m, str) for m in modalities):
        raise ConfigError(f"{path}: roles.{name}.modalities must be a list of names")
    known = {"model", "description", "temperature", "dimensions", "modalities"}
    return RoleConfig(
        name=name,
        model=model.strip(),
        description=str(data.get("description") or ""),
        temperature=None if temperature is None else float(temperature),
        dimensions=dimensions,
        modalities=tuple(modalities),
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_models_config(path: str | Path | None = None) -> ModelsConfig:
    """Read and validate the models config.

    Path order: the ``path`` argument, then ``$SOGICE_MODELS_CONFIG``, then
    ``config/models.yaml`` in the repository.
    """
    chosen = Path(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
    try:
        text = chosen.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"models config not found: {chosen}") from None
    data = parse_simple_yaml(text)

    ollama = data.get("ollama")
    if not isinstance(ollama, dict):
        raise ConfigError(f"{chosen}: missing 'ollama' section")
    base_url = ollama.get("base_url")
    if not isinstance(base_url, str) or not re.match(r"^https?://", base_url):
        raise ConfigError(f"{chosen}: ollama.base_url must be an http(s) URL")
    timeout = ollama.get("timeout_seconds", 600)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError(f"{chosen}: ollama.timeout_seconds must be a positive number")

    roles_data = data.get("roles")
    if not isinstance(roles_data, dict):
        raise ConfigError(f"{chosen}: missing 'roles' section")
    roles = {name: _role(name, value, chosen) for name, value in roles_data.items()}
    missing = [name for name in REQUIRED_ROLES if name not in roles]
    if missing:
        raise ConfigError(f"{chosen}: missing model roles: {', '.join(missing)}")
    if roles["embedding"].dimensions is None:
        raise ConfigError(f"{chosen}: roles.embedding.dimensions is required")

    return ModelsConfig(base_url=base_url.rstrip("/"), timeout_seconds=float(timeout),
                        roles=roles, path=chosen)
