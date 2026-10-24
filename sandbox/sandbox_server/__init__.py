"""Isolated Python runner for the analyst agent's run_python tool.

Layers, outermost first: the container (no network, read-only root, non-root user, memory, CPU and
pid limits), a child process per job with rlimits and a wall-clock kill, an audit hook and
restricted builtins inside the child, and a static check of the code before it is run.
"""
