import json
import os
import warnings
from dataclasses import dataclass, field, fields, is_dataclass, MISSING
from enum import Enum
from typing import List, Type, TypeVar, get_args, get_origin

from flops.const import REQUEST_TIMEOUT
from flops.schemas import Permission


T = TypeVar("T")


def from_dict(cls: Type[T], data: dict) -> T:
    """Generic recursive dict-to-dataclass builder."""
    if not is_dataclass(cls):
        return data

    kwargs = {}

    for f in fields(cls):
        if f.name not in data:
            if f.default is MISSING and f.default_factory is MISSING:
                raise ValueError(f"Missing required field: {f.name}")
            continue

        field_value = data[f.name]

        if field_value is None:
            if f.default is not MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not MISSING:
                kwargs[f.name] = f.default_factory()
            else:
                kwargs[f.name] = None
            continue

        field_type = f.type
        origin = get_origin(field_type)

        if origin is list or origin is List:
            inner_type = get_args(field_type)[0]
            kwargs[f.name] = [from_dict(inner_type, item) for item in field_value]
        elif origin is dict:
            _, value_type = get_args(field_type)
            kwargs[f.name] = {
                k: from_dict(value_type, v) if is_dataclass(value_type) else v
                for k, v in field_value.items()
            }
        elif isinstance(field_type, type) and issubclass(field_type, Enum):
            kwargs[f.name] = field_type(field_value)
        elif is_dataclass(field_type):
            kwargs[f.name] = from_dict(field_type, field_value)
        else:
            kwargs[f.name] = field_value

    return cls(**kwargs)


@dataclass
class ModelConfig:
    max_tokens: int = 8192
    context_size: int = 200 * 1024
    thinking: bool = True
    request_timeout: int = REQUEST_TIMEOUT


@dataclass
class ProviderConfig:
    api_key: str
    base_url: str
    models: dict[str, ModelConfig]
    api_format: str = "auto"


@dataclass
class AgentConfig:
    model: str
    max_turns: int = 10
    workspace: str = ""

    def __post_init__(self):
        if not self.workspace:
            self.workspace = os.getcwd()


@dataclass
class LogConfig:
    level: str = "INFO"


@dataclass
class MemoryConfig:
    distill_interval: int = 10  # N turns between distillations
    enabled: bool = True


@dataclass
class SkillsConfig:
    paths: list[str] = field(default_factory=list)


@dataclass
class ToolConfig:
    permission: Permission = Permission.STANDARD

    def __post_init__(self):
        if isinstance(self.permission, str):
            self.permission = Permission(self.permission)


@dataclass
class Config:
    name: str
    providers: dict[str, ProviderConfig]
    agent: AgentConfig
    log: LogConfig = field(default_factory=LogConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)

    @classmethod
    def from_json(cls, path: str) -> "Config":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Config file not found: {path}\n"
                f"Please create a config file or specify one with --config."
            )

        config = from_dict(cls, data)
        config.validate()
        return config

    def validate(self):
        """Validate configuration and provide helpful error messages."""

        # Validate providers section
        if not self.providers:
            raise ValueError("At least one provider must be configured in 'providers'")

        valid_api_formats = {"anthropic", "openai", "auto"}
        all_models = []

        for provider_name, pc in self.providers.items():
            # Check api_format
            if pc.api_format not in valid_api_formats:
                raise ValueError(
                    f"Invalid api_format '{pc.api_format}' for provider '{provider_name}'. "
                    f"Must be one of: {valid_api_formats}"
                )

            # Check api_key
            if not pc.api_key:
                raise ValueError(
                    f"API key for provider '{provider_name}' is empty. "
                    "Please edit your config.json and set the api_key."
                )

            # Check base_url
            if not pc.base_url:
                raise ValueError(
                    f"base_url for provider '{provider_name}' is not configured. "
                    "Please edit your config.json and set the base_url."
                )

            # Check models
            if not pc.models:
                raise ValueError(
                    f"Provider '{provider_name}' must have at least one model configured"
                )

            for model_name, mc in pc.models.items():
                full_name = f"{provider_name}:{model_name}"
                all_models.append(full_name)

                # Check max_tokens
                if mc.max_tokens <= 0:
                    raise ValueError(f"max_tokens for model '{full_name}' must be > 0")
                if mc.max_tokens > mc.context_size:
                    raise ValueError(
                        f"max_tokens ({mc.max_tokens}) for model '{full_name}' "
                        f"exceeds context_size ({mc.context_size})"
                    )

                # Check context_size
                if mc.context_size <= 0:
                    raise ValueError(f"context_size for model '{full_name}' must be > 0")

        # Validate agent section
        if not self.agent.model:
            raise ValueError("Agent model must be specified")

        if self.agent.model not in all_models:
            raise ValueError(
                f"Agent model '{self.agent.model}' not found in providers config. "
                f"Available models: {all_models}"
            )

        if self.agent.max_turns <= 0:
            raise ValueError("max_turns must be > 0")

        # Validate log section
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if self.log and self.log.level:
            if self.log.level not in valid_log_levels:
                raise ValueError(
                    f"Invalid log level '{self.log.level}'. " f"Must be one of: {valid_log_levels}"
                )

        # Validate skills section
        if self.skills and self.skills.paths:
            for path in self.skills.paths:
                if not os.path.exists(path):
                    warnings.warn(
                        f"Skills path '{path}' does not exist. "
                        "It will be skipped during skill loading."
                    )

        # Validate tool section
        # Enum validation is handled by Permission() constructor in from_dict
