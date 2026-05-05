import json
import os
import tempfile
from pathlib import Path

from flops.config import Config


def _config_path() -> str:
    return str(Path(__file__).parent / "test_config.json")


def test_config():
    """Load minimal valid config and verify all fields."""
    v = Config.from_json(_config_path())
    assert v.name == "flops"
    assert len(v.providers) >= 1
    assert "test-provider" in v.providers
    assert v.agent.model == "test-provider:test-model"
    assert v.agent.max_turns == 10
    assert v.log.level == "INFO"


def test_validate_empty_providers():
    """Config with no providers should raise ValueError."""
    config_data = {
        "name": "flops",
        "providers": {},
        "agent": {"model": "test"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        f.flush()
        try:
            Config.from_json(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "At least one" in str(e)
        finally:
            os.unlink(f.name)


def test_validate_model_not_found():
    """Agent model not in providers should raise ValueError."""
    config_data = {
        "name": "flops",
        "providers": {
            "provider-a": {
                "api_key": "key-a",
                "base_url": "http://a.com",
                "models": {"Model-A": {}},
            }
        },
        "agent": {"model": "NonExistent"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        f.flush()
        try:
            Config.from_json(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)
            assert "Model-A" in str(e)
        finally:
            os.unlink(f.name)


def test_validate_bad_api_format():
    """Invalid api_format should raise ValueError."""
    config_data = {
        "name": "flops",
        "providers": {
            "bad": {
                "api_format": "bad-format",
                "api_key": "key",
                "base_url": "http://a.com",
                "models": {"Model-A": {}},
            }
        },
        "agent": {"model": "bad:Model-A"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        f.flush()
        try:
            Config.from_json(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "bad-format" in str(e)
        finally:
            os.unlink(f.name)


def test_validate_max_tokens_exceeds_context():
    """max_tokens > context_size should raise ValueError."""
    config_data = {
        "name": "flops",
        "providers": {
            "p": {
                "api_key": "key",
                "base_url": "http://a.com",
                "models": {"M": {"max_tokens": 50000, "context_size": 10000}},
            }
        },
        "agent": {"model": "p:M"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        f.flush()
        try:
            Config.from_json(f.name)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "exceeds" in str(e)
        finally:
            os.unlink(f.name)


def test_engine_init_with_test_config():
    """Engine initializes correctly with test config (no network needed)."""
    from flops.engine import Engine

    config = Config.from_json(_config_path())
    engine = Engine(config)
    assert engine.model == "test-provider:test-model"
    assert engine.workspace is not None
    assert engine.session_id is not None
