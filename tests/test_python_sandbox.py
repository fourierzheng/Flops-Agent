import importlib

from flops.tools.python_tool import _build_safe_builtins, _SAFE_MODULES, _BLOCKED_BUILTINS


def test_builtins_blocked():
    """Dangerous builtins are removed from safe builtins."""
    safe_builtins = _build_safe_builtins()
    for name in _BLOCKED_BUILTINS:
        assert name not in safe_builtins, f"{name} should be blocked"


def test_safe_import_allowed():
    """Whitelisted modules can be imported in the sandbox."""
    safe_builtins = _build_safe_builtins()
    for mod_name in sorted(_SAFE_MODULES):
        # Simulate what happens inside exec()
        namespace = {"__builtins__": safe_builtins}
        try:
            exec(f"import {mod_name}", namespace)
            assert mod_name in namespace, f"{mod_name} should be importable"
        except ImportError as e:
            assert False, f"Safe module {mod_name} was blocked: {e}"


def test_unsafe_import_blocked():
    """Non-whitelisted modules raise ImportError in the sandbox."""
    blocked = ["os", "subprocess", "pathlib", "sys", "shutil", "socket"]
    safe_builtins = _build_safe_builtins()
    for mod_name in blocked:
        namespace = {"__builtins__": safe_builtins}
        try:
            exec(f"import {mod_name}", namespace)
            assert False, f"{mod_name} should be blocked but was imported"
        except ImportError:
            pass  # Expected


def test_submodule_of_safe_module_allowed():
    """Submodules of whitelisted modules (e.g. json.decoder) are allowed."""
    safe_builtins = _build_safe_builtins()
    namespace = {"__builtins__": safe_builtins}
    exec("import json.decoder", namespace)
    assert "json" in namespace


def test_submodule_of_unsafe_module_blocked():
    """Even submodules of blocked modules are blocked."""
    safe_builtins = _build_safe_builtins()
    namespace = {"__builtins__": safe_builtins}
    try:
        exec("import os.path", namespace)
        assert False, "os.path should be blocked"
    except ImportError:
        pass


def test_blocked_builtins_not_in_exec():
    """eval/exec/compile are not accessible inside sandbox exec."""
    safe_builtins = _build_safe_builtins()
    namespace = {"__builtins__": safe_builtins}
    # These should raise NameError inside the sandbox
    for name in ("eval", "exec", "compile"):
        try:
            exec(f"print({name})", namespace)
            assert False, f"{name} should not be accessible"
        except NameError:
            pass
