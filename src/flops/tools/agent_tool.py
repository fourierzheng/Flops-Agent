from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool, get_tool_schemas


class AgentMode(str, Enum):
    EXPLORE = "explore"
    PLAN = "plan"
    GENERAL = "general"


class AgentParams(BaseModel):
    task: str = Field(description="The task description for the sub-agent to execute.")
    mode: AgentMode = Field(
        default=AgentMode.GENERAL,
        description="Sub-agent mode: 'explore' (read-only code search), 'plan' (read-only analysis & planning), 'general' (full access, can edit files).",
    )


_EXPLORE_PROMPT = """You are a file search specialist. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files
- Modifying existing files
- Deleting files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use FileRead when you know the specific file path you need to read
- Adapt your search approach based on the thoroughness level specified by the caller
- Make efficient use of the tools at your disposal
- Wherever possible, spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly."""

_PLAN_PROMPT = """You are a Plan agent — a READ-ONLY sub-agent specialized for designing implementation plans.

IMPORTANT CONSTRAINTS:
- You are READ-ONLY. You only have access to read/search tools.
- Do NOT attempt to modify any files.

Your job:
- Analyze the codebase to understand the current architecture
- Design a step-by-step implementation plan
- Identify critical files that need modification
- Consider architectural trade-offs

Return a structured plan with:
1. Summary of current state
2. Step-by-step implementation steps
3. Critical files for implementation
4. Potential risks or considerations"""

_GENERAL_PROMPT = """You are a coding assistant working on a delegated subtask. You should use the tools available to complete the task. Complete the task fully — don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings.

Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research and implementation tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives. Use FileRead when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one."""

_PROMPTS = {
    AgentMode.EXPLORE: _EXPLORE_PROMPT,
    AgentMode.PLAN: _PLAN_PROMPT,
    AgentMode.GENERAL: _GENERAL_PROMPT,
}

_READ_ONLY_TOOLS = ["Glob", "Grep", "FileRead", "List", "Web", "Weather"]
_PLAN_TOOLS = ["Glob", "Grep", "FileRead", "List", "Web", "Weather", "Mem", "Skill"]

_TOOLS = {
    AgentMode.EXPLORE: _READ_ONLY_TOOLS,
    AgentMode.PLAN: _PLAN_TOOLS,
    AgentMode.GENERAL: None,  # all tools
}


@tool
class AgentTool(Tool):
    """Delegate a task to a sub-agent. Three modes available: 'explore' for read-only code search, 'plan' for read-only analysis & planning, 'general' for full access tasks. Use this for complex multi-step tasks that can be worked on in isolation."""

    params_model = AgentParams

    def render(self, tool_input: dict) -> str:
        mode = tool_input.get("mode", "general")
        return f"🤖 Agent[{mode}] is running... ⏳"

    async def execute(self, ctx: ToolContext, params: AgentParams) -> ToolResult:
        logger.info(f"Sub-agent [{params.mode.value}] task: {params.task[:100]}...")
        prompt = _PROMPTS[params.mode]
        tools = _TOOLS[params.mode]
        if tools is None:
            # General mode: exclude Agent to prevent infinite recursion
            tools = [s["name"] for s in get_tool_schemas() if s["name"] != "Agent"]

        result = ToolResult(content="")
        result.stream = ctx.stream_chat(prompt, params.task, tools)  # type: ignore
        return result
