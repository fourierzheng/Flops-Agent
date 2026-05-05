from flops.tools import get_tool_schemas


def test_tools_registered():
    """All expected tools are registered with valid schemas."""
    schemas = get_tool_schemas()
    names = [s["name"] for s in schemas]

    expected_tools = {
        "FileRead",
        "FileWrite",
        "FileEdit",
        "Rm",
        "Glob",
        "Grep",
        "List",
        "Shell",
        "Python",
        "Web",
        "Weather",
        "Skill",
    }
    for name in expected_tools:
        assert name in names, f"Missing tool: {name}"
    assert len(schemas) >= len(expected_tools)


def test_tool_schema_structure():
    """Each tool schema has required fields."""
    schemas = get_tool_schemas()
    for s in schemas:
        assert "name" in s, f"Tool missing 'name': {s}"
        assert "description" in s, f"Tool {s['name']} missing 'description'"
        assert "input_schema" in s, f"Tool {s['name']} missing 'input_schema'"
        assert s["description"], f"Tool {s['name']} has empty description"
        assert (
            s["input_schema"].get("type") == "object"
        ), f"Tool {s['name']} input_schema should be object"


def test_tool_input_schema_properties():
    """Tool input schemas define their parameters."""
    schemas = get_tool_schemas()
    schemas_by_name = {s["name"]: s for s in schemas}

    # ShellTool should have command + optional cwd
    shell = schemas_by_name["Shell"]
    props = shell["input_schema"]["properties"]
    assert "command" in props
    assert props["command"]["type"] == "string"

    # FileReadTool should have file_path + optional start_line/num_lines
    fr = schemas_by_name["FileRead"]
    fr_props = fr["input_schema"]["properties"]
    assert "file_path" in fr_props
    assert "start_line" in fr_props

    # WeatherTool should have city
    weather = schemas_by_name["Weather"]
    w_props = weather["input_schema"]["properties"]
    assert "city" in w_props
