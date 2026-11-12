import pytest

from archive import config
from archive.config import ConfigError, load_models_config, parse_simple_yaml


def test_repository_config_has_three_roles():
    cfg = load_models_config(config.DEFAULT_PATH)
    assert cfg.base_url == "http://127.0.0.1:11434"
    assert set(config.REQUIRED_ROLES) <= set(cfg.roles)
    assert cfg.model_for("reading")
    assert cfg.model_for("audit")
    # Since 10 Nov the 31B both reads and audits (Sérgio's decision); trial roles are named trial_*;
    # since 11 Nov a small model copies titles from first pages (archive/titles.py); since 12 Nov the
    # reading pair also tags the statements (archive/tag.py).
    assert all(name in config.REQUIRED_ROLES or name in ("reading_second", "titles", "tagging", "tagging_second")
               or name.startswith("trial_") for name in cfg.roles)
    assert cfg.model_for("reading_second") != cfg.model_for("reading")   # two different readers
    assert cfg.model_for("tagging_second") != cfg.model_for("tagging")   # two different taggers
    embedding = cfg.role("embedding")
    assert embedding.dimensions == 768
    assert "text" in embedding.modalities


def test_model_tags_with_colons_stay_strings():
    cfg = load_models_config(config.DEFAULT_PATH)
    assert ":" in cfg.model_for("reading")  # e.g. gemma4:12b, not split as a key


def test_env_var_overrides_path(tmp_path, monkeypatch):
    path = tmp_path / "models.yaml"
    path.write_text(
        "ollama:\n  base_url: http://localhost:9999/\n"
        "roles:\n"
        "  reading:\n    model: a:1\n"
        "  audit:\n    model: b:2\n"
        "  embedding:\n    model: c\n    dimensions: 256\n",
        encoding="utf-8")
    monkeypatch.setenv(config.ENV_VAR, str(path))
    cfg = load_models_config()
    assert cfg.base_url == "http://localhost:9999"
    assert cfg.model_for("audit") == "b:2"
    assert cfg.role("embedding").dimensions == 256
    with pytest.raises(ConfigError):
        cfg.role("vision")


def test_parser_subset():
    data = parse_simple_yaml(
        "# comment\n"
        "a:\n"
        "  b: 1\n"
        "  c: 2.5\n"
        "  d: true\n"
        "  e: null\n"
        "  f: \"quoted # not a comment\"  # a comment\n"
        "  g: [x, 'y z', 3]\n"
        "  nested:\n"
        "    deep: http://h:1/p\n"
        "  empty:\n"
        "top: value\n")
    assert data == {
        "a": {"b": 1, "c": 2.5, "d": True, "e": None, "f": "quoted # not a comment",
              "g": ["x", "y z", 3], "nested": {"deep": "http://h:1/p"}, "empty": None},
        "top": "value",
    }


@pytest.mark.parametrize("text", [
    "a:\n  - item\n",            # block list
    "a:\n\tb: 1\n",              # tab indent
    "a:\n    b: 1\n  c: 2\n",    # inconsistent indent
    "a: 1\na: 2\n",              # duplicate key
    "a: [x, y\n",                # unclosed list
    "a: 'open\n",                # unclosed quote
    "just text\n",               # not key: value
    "a: &anchor x\n",            # anchors unsupported
])
def test_parser_rejects_unsupported_yaml(text):
    with pytest.raises(ConfigError):
        parse_simple_yaml(text)


@pytest.mark.parametrize("text, message", [
    ("roles:\n  reading:\n    model: a\n", "ollama"),
    ("ollama:\n  base_url: 127.0.0.1\nroles:\n  reading:\n    model: a\n", "base_url"),
    ("ollama:\n  base_url: http://x\nroles:\n  reading:\n    model: a\n", "missing model roles"),
    ("ollama:\n  base_url: http://x\nroles:\n  reading:\n    model: a\n  audit:\n    model: b\n"
     "  embedding:\n    model: c\n", "dimensions"),
    ("ollama:\n  base_url: http://x\nroles:\n  reading:\n    model: a\n  audit:\n    model: b\n"
     "  embedding:\n    model: c\n    dimensions: -1\n", "dimensions"),
])
def test_validation_errors(tmp_path, text, message):
    path = tmp_path / "models.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_models_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_models_config(tmp_path / "nope.yaml")
