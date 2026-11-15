"""Frozen, offline prompt variants for a no-cache creative-generation experiment.

This research helper neither calls a model nor changes production generation,
repair, native compilation, or cache behavior. The experiment runner must disable
its cache; no production cache integration is provided here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from yescc.model.base import Message
from yescc.model.prompts import generate_messages

GenerationVariant = Literal["baseline", "examples", "facts"]
GENERATION_PROMPT_VERSION = "creative-generation-support-1"
EXAMPLES_PATH = Path(__file__).with_name("creative_generation_examples.json")

# Freeze before model calls. These instructions must not be tuned against the
# resulting works; revisions belong to a separately frozen experiment.
EXAMPLES_INSTRUCTION = (
    "The two examples below demonstrate self-consistent ideas. Do not copy their "
    "topics or wording; still create for the current input."
)
FACTS_INSTRUCTION = (
    "These are local measurements of the exact current input, without normalization. "
    "codepoint_count is Python len(text), not visible characters, grapheme clusters, "
    "or display width. utf8_byte_count is len(text.encode('utf-8')). line_count is "
    "len(text.splitlines()), using Python splitlines semantics. The other counts "
    "count only the named codepoints: ASCII space, tab, CR, LF, and U+3000 space. "
    "Use these measurements only to avoid false factual claims; they are not output "
    "requirements or evidence of intent. Do not print a statistics report; keep "
    "creating for the current input."
)
EXAMPLES_INSTRUCTION_SHA256 = hashlib.sha256(EXAMPLES_INSTRUCTION.encode("utf-8")).hexdigest()
FACTS_INSTRUCTION_SHA256 = hashlib.sha256(FACTS_INSTRUCTION.encode("utf-8")).hexdigest()


def input_facts(text: str) -> dict[str, int]:
    """Measure the original string, preserving all whitespace and Unicode forms."""
    return {
        "codepoint_count": len(text),
        "utf8_byte_count": len(text.encode("utf-8")),
        "line_count": len(text.splitlines()),
        "ascii_space_count": text.count(" "),
        "tab_count": text.count("\t"),
        "cr_count": text.count("\r"),
        "lf_count": text.count("\n"),
        "u3000_space_count": text.count("\u3000"),
    }


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict[str, str]]:
    """Load exactly two UTF-8 examples with the frozen, small data schema."""
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 2:
        raise ValueError("examples must be a list of exactly two examples")
    fields = {"id", "inspiration", "source", "stdout", "note"}
    examples: list[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("each example must have exactly id/inspiration/source/stdout/note")
        example: dict[str, str] = {}
        for field in sorted(fields):
            value = entry[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"example {field} must be a nonempty string")
            value.encode("utf-8")
            example[field] = value
        examples.append(example)
    if examples[0]["id"] == examples[1]["id"]:
        raise ValueError("example ids must be unique")
    return examples


def generation_messages(
    text: str, *, variant: GenerationVariant, feedback: str = ""
) -> list[Message]:
    """Append one frozen supplement; baseline is the unchanged creative prompt."""
    if variant not in {"baseline", "examples", "facts"}:
        raise ValueError(f"unknown generation variant: {variant}")
    messages = generate_messages(text, creative=True, feedback=feedback)
    if variant == "baseline":
        return messages
    if variant == "examples":
        instruction = EXAMPLES_INSTRUCTION
        payload: object = load_examples()
        label = "Examples (JSON data):"
    else:
        instruction = FACTS_INSTRUCTION
        payload = input_facts(text)
        label = "Local input measurements (JSON data):"
    supplement = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    previous = messages[-1]
    messages[-1] = Message(
        role=previous.role,
        content=f"{previous.content}\n{instruction}\n{label}\n{supplement}\n",
    )
    return messages
