"""Cyclomatic complexity per function/method, excluding nested defs from their parent.

Usage:
    python complexity.py <path/to/file.py> [qualname-substring ...]

Without qualname filters, prints the 15 highest-complexity functions in the
file. With filters, prints only functions whose dotted qualname (e.g.
``ClassName.method_name``) contains one of the given substrings -- still
capped to the top 15 by complexity, so narrow the filter if your target
isn't in the top 15.

Why not radon/mccabe: this repo doesn't have them as a dependency, and pulling
one in for a one-off audit isn't worth it. This is ~50 lines of stdlib `ast`.

The one thing worth getting right that a naive version gets wrong: a nested
`def`/`async def`/lambda's branches must NOT count toward the enclosing
function's complexity, or splitting a god-method into an outer dispatcher
plus an inner closure looks like it changed nothing.
"""

import ast
import sys

_BRANCH_TYPES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
)


def complexity_of(func_node: ast.AST) -> int:
    """Cyclomatic complexity of func_node's own body, excluding nested defs."""
    count = 1

    def walk(node: ast.AST, top: ast.AST) -> None:
        nonlocal count
        for child in ast.iter_child_nodes(node):
            if child is not top and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue  # nested function: measured separately
            if isinstance(child, _BRANCH_TYPES):
                count += 1
            if isinstance(child, ast.BoolOp):
                count += len(child.values) - 1
            walk(child, top)

    walk(func_node, func_node)
    return count


def _walk_defs(node: ast.AST, prefix: str = ""):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _walk_defs(child, prefix + child.name + ".")
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = prefix + child.name
            lines = child.end_lineno - child.lineno + 1
            yield (name, child.lineno, child.end_lineno, lines, complexity_of(child))
            yield from _walk_defs(child, prefix + child.name + ".")


def main(path: str, qualnames: list[str] | None = None) -> None:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    results = sorted(_walk_defs(tree), key=lambda r: -r[4])
    for name, start, end, lines, cx in results[:15]:
        if qualnames and not any(q in name for q in qualnames):
            continue
        print(f"{cx:>4}  {lines:>5}L  {start:>5}-{end:<5}  {name}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] if len(sys.argv) > 2 else None)
