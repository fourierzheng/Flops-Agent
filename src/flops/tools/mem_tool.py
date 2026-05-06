from pydantic import BaseModel, Field

from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class MemParams(BaseModel):
    domain: str | None = Field(
        default=None, description="Filter by domain: user/project/habit/decision/context"
    )
    key: str | None = Field(default=None, description="Filter by exact key name")
    search: str | None = Field(default=None, description="Search in key and value (fuzzy)")


@tool
class MemTool(Tool):
    """Query long-term memory. Use this to recall user preferences, project facts, decisions, or habits from past conversations."""

    params_model = MemParams

    async def execute(self, ctx: ToolContext, params: MemParams) -> ToolResult:
        results = ctx.memory.query(domain=params.domain, key=params.key, search=params.search)
        if not results:
            return ToolResult(
                content="No matching facts found in memory. Facts are accumulated over time through automatic distillation."
            )

        lines = ["Matching facts:"]
        for r in results:
            lines.append(
                f"  [{r['domain']}] {r['key']} = {r['value']} " f"(confidence: {r['confidence']}/5)"
            )
        return ToolResult(content="\n".join(lines))
