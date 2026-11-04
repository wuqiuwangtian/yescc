"""C 容错解析：记录语法错误区间，供报告与后续修复使用。"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_c
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tree_sitter_c.language())


@dataclass(frozen=True)
class SyntaxError:
    start: int
    end: int
    node_type: str


@dataclass(frozen=True)
class ParseInfo:
    has_error: bool
    errors: tuple[SyntaxError, ...]


def _byte_to_char(text: str, byte_offset: int) -> int:
    return len(text.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore"))


def _collect_errors(node: Node, limit: int = 32) -> list[Node]:
    errors: list[Node] = []
    if node.type == "ERROR":
        errors.append(node)
    for child in node.children:
        if len(errors) >= limit:
            break
        errors.extend(_collect_errors(child, limit - len(errors)))
    return errors


def parse_c(text: str) -> ParseInfo:
    """用 Tree-sitter 解析 C；语法错误时仍返回可用的错误节点。"""
    parser = Parser(_LANGUAGE)
    tree = parser.parse(text.encode("utf-8"))
    root = tree.root_node
    nodes = _collect_errors(root)
    errors = tuple(
        SyntaxError(
            start=_byte_to_char(text, node.start_byte),
            end=_byte_to_char(text, node.end_byte),
            node_type=node.type,
        )
        for node in nodes
    )
    return ParseInfo(has_error=root.has_error, errors=errors)
