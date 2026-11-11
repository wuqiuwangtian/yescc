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


def missing_semicolons(text: str) -> list[int]:
    """返回语法树明确缺失的语句分号；不处理 for 头部、宏或非语句节点。"""
    tree = Parser(_LANGUAGE).parse(text.encode("utf-8"))
    positions: list[int] = []
    stack = [tree.root_node]
    allowed = {
        "expression_statement",
        "return_statement",
        "declaration",
        "break_statement",
        "continue_statement",
    }
    while stack and len(positions) < 8:
        node = stack.pop()
        if (
            node.is_missing
            and node.type == ";"
            and node.parent is not None
            and node.parent.type in allowed
        ):
            ancestor: Node | None = node.parent
            in_header = False
            while ancestor is not None:
                if ancestor.type == "for_statement":
                    body = ancestor.child_by_field_name("body")
                    if body is None or node.start_byte < body.start_byte:
                        in_header = True
                    break
                ancestor = ancestor.parent
            if in_header:
                continue
            position = _byte_to_char(text, node.start_byte)
            before = text[:position].splitlines()
            if before and not before[-1].lstrip().startswith("#"):
                # 多行宏也不自动修复。
                line = len(before) - 1
                if not (line and before[line - 1].rstrip().endswith("\\")):
                    positions.append(position)
        stack.extend(reversed(node.children))
    return sorted(set(positions))
