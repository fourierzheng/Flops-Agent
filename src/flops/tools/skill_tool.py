from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class SkillParams(BaseModel):
    skill_name: str = Field(description="Skill name to load.")


@tool
class SkillTool(Tool):
    """Read a skill by name"""

    params_model = SkillParams

    def render(self, tool_input: dict) -> str:
        return f"⚙️ Skill({tool_input.get('skill_name')})"

    async def execute(self, ctx: ToolContext, params: SkillParams) -> ToolResult:
        skill_name = params.skill_name
        logger.info(f"Loading skill: {skill_name}")
        try:
            skill = ctx.skills.get(skill_name)
        except KeyError:
            logger.warning(f"Skill not found: {skill_name}")
            return ToolResult(f"Not found skill: {skill_name}", True)
        logger.info(f"Skill loaded: {skill_name} from {skill.path}")
        return ToolResult(skill.path.read_text(), False)
