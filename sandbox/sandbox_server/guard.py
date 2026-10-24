"""Static check of submitted code: an extra layer, never the only one.

The check is an allow-list of modules plus a deny-list of names and attributes that are the usual
first step of an escape (dunder walking, dynamic imports, file access). It is easy to be stricter
than needed here because the child process enforces the same rules again at run time.
"""

import ast

MAX_CODE_CHARS = 20_000

ALLOWED_MODULES = frozenset(
    {
        "pandas",
        "numpy",
        "scipy",
        "statsmodels",
        "sklearn",
        "math",
        "statistics",
        "json",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "re",
        "decimal",
        "fractions",
        "random",
        "operator",
        "string",
        "typing",
        "dataclasses",
    }
)

BLOCKED_NAMES = frozenset(
    {
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "eval",
        "exec",
        "compile",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "input",
        "breakpoint",
        "help",
        "memoryview",
        "exit",
        "quit",
    }
)

# Modules that are reachable as attributes of allowed libraries (pd.io.common.os, np.sys, ...).
BLOCKED_ATTRIBUTES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "ctypes",
        "shutil",
        "importlib",
        "builtins",
        "pathlib",
        "io",
        "signal",
        "threading",
        "multiprocessing",
        "pickle",
        "marshal",
        "system",
        "popen",
        "load",
        "save",
        "savez",
        "savetxt",
        "loadtxt",
        "fromfile",
        "tofile",
        "genfromtxt",
        "memmap",
        "to_pickle",
        "to_parquet",
        "to_excel",
        "to_hdf",
        "to_feather",
        "to_stata",
        "to_sql",
        "to_clipboard",
    }
)


def check(code: str) -> list[str]:
    """Return the problems found in `code` (an empty list means it may run)."""
    if len(code) > MAX_CODE_CHARS:
        return [f"code is longer than {MAX_CODE_CHARS} characters"]
    if "\x00" in code:
        return ["code contains a NUL byte"]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]
    problems: list[str] = []
    for node in ast.walk(tree):
        problems.extend(_node_problems(node))
    return sorted(set(problems))


def _node_problems(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [_module_problem(alias.name) for alias in node.names if not _allowed(alias.name)]
    if isinstance(node, ast.ImportFrom):
        if node.level or not node.module or not _allowed(node.module):
            return [_module_problem(node.module or ".")]
        return []
    if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
        return [f"'{node.id}' is not allowed"]
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            return [f"private attribute '.{node.attr}' is not allowed"]
        if node.attr in BLOCKED_ATTRIBUTES or node.attr.startswith("read_"):
            return [f"attribute '.{node.attr}' is not allowed"]
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
        return ["strings containing '__' are not allowed"]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return ["global and nonlocal are not allowed"]
    return []


def _allowed(module: str) -> bool:
    return module.split(".")[0] in ALLOWED_MODULES


def _module_problem(module: str) -> str:
    return f"import of '{module}' is not allowed (allowed: {', '.join(sorted(ALLOWED_MODULES))})"
