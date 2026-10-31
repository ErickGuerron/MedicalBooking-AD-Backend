"""Helpers for executing reviewed T-SQL scripts."""

from __future__ import annotations


def split_batches(script: str) -> list[str]:
    """Split a T-SQL script into batches on standalone ``GO`` separator lines."""
    batches: list[str] = []
    current: list[str] = []
    for line in script.splitlines():
        if line.strip().upper() == "GO":
            if "".join(current).strip():
                batches.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if "".join(current).strip():
        batches.append("\n".join(current))
    return batches
