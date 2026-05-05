from pathlib import Path
import re

import yaml

from flops.logger import logger
from flops.registry import Registry
from flops.schemas import Skill


def _parse_skill(skill_path: Path):
    content = skill_path.read_text()
    name = None
    description = None
    match = re.search(r"^---\s*\n(.*?)\n---", content, re.S)
    if match:
        yaml_block = match.group(1)
        data = yaml.safe_load(yaml_block)
        name = data.get("name")
        description = data.get("description")

    else:
        lines = [line for line in content.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            if line.startswith("# "):
                name = line[2:].strip()
                # description is the next non-empty line after the heading
                description = lines[i + 1].strip() if i + 1 < len(lines) else ""
                break
    if name is None or description is None:
        logger.warning(f"Failed to parse skill from {skill_path}")
        return None
    return Skill(name, description, skill_path)


def load_skills(search_paths: list[str]) -> Registry[Skill]:
    logger.info(f"Loading skills from: {search_paths}")
    registry: Registry[Skill] = Registry()
    for sp in search_paths:
        skill_files = list(Path(sp).expanduser().rglob("SKILL.md"))
        logger.debug(f"Found {len(skill_files)} skill files in {sp}")
        for p in skill_files:
            skill = _parse_skill(p)
            if skill:
                registry.register(skill.name, skill)
                logger.info(f"Registered skill: {skill.name}")
    logger.info(f"Total skills loaded: {len(registry)}")
    return registry
