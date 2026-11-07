"""Exact, symbol-scoped mutations; this module is never copied into worker images."""
import ast
import json
from pathlib import Path


def catalog():
    return json.loads(Path(__file__).with_name("mutations.json").read_text())


def symbol_node(text, symbol):
    node = ast.parse(text)
    for name in symbol.split("."):
        node = next(item for item in node.body
                    if isinstance(item, (ast.ClassDef, ast.FunctionDef)) and item.name == name)
    return node


def apply_mutation(text, mutation):
    node = symbol_node(text, mutation["symbol"])
    lines = text.splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1]))
    end = sum(map(len, lines[:node.end_lineno]))
    section = text[start:end]
    replacements = mutation["replacements"]
    for index, replacement in enumerate(replacements):
        old = replacement["old"]
        if section.count(old) != 1:
            raise ValueError(f"Bug {mutation['id']}: expected exactly one match for {old!r}")
        section = section.replace(old, f"__OSCAR_MUTATION_{index}__", 1)
    for index, replacement in enumerate(replacements):
        section = section.replace(f"__OSCAR_MUTATION_{index}__", replacement["new"], 1)
    result = text[:start] + section + text[end:]
    ast.parse(result)
    if result == text:
        raise ValueError("Mutation did not change the source")
    return result
