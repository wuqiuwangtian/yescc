"""unified diff 严格应用测试。"""

from __future__ import annotations

import pytest

from yescc.patch import PatchError, apply_unified_diff

ORIGINAL = '#include <stdio.h>\nint main(void) { printf("hi\\n") return 0; }\n'

DIFF = """--- a/main.c
+++ b/main.c
@@ -1,2 +1,2 @@
 #include <stdio.h>
-int main(void) { printf("hi\\n") return 0; }
+int main(void) { printf("hi\\n"); return 0; }
"""


def test_apply_simple_diff() -> None:
    patched = apply_unified_diff(ORIGINAL, DIFF)
    assert 'printf("hi\\n"); return 0;' in patched


def test_reject_context_mismatch() -> None:
    with pytest.raises(PatchError):
        apply_unified_diff(ORIGINAL.replace("hi", "bye"), DIFF)


def test_reject_empty_diff() -> None:
    with pytest.raises(PatchError):
        apply_unified_diff(ORIGINAL, "no hunks here")


def test_reject_whole_file_rewrite() -> None:
    original = "".join(f"int value_{index} = {index};\n" for index in range(20))
    diff = "".join(
        f"-int value_{index} = {index};\n+int value_{index} = {index + 1};\n" for index in range(20)
    )
    wrapped = f"--- a/main.c\n+++ b/main.c\n@@ -1,20 +1,20 @@\n{diff}"
    with pytest.raises(PatchError):
        apply_unified_diff(original, wrapped)


def test_relocate_and_recount_exact_context() -> None:
    notes: list[str] = []
    actual = apply_unified_diff(
        "prefix\nkeep\nbroken\nend\n",
        "@@ -1,99 +1,88 @@\n keep\n-broken\n+fixed\n end\n",
        normalizations=notes,
    )
    assert actual == "prefix\nkeep\nfixed\nend\n"
    assert notes and "unique exact context" in notes[0]


@pytest.mark.parametrize(
    ("original", "diff"),
    [
        ("same\nsame\n", "@@ -1 +1 @@\n-same\n+fixed\n"),
        ("a\nb\nc\n", "@@ -2 +2 @@\n-b\n+x\n@@ -2 +2 @@\n-b\n+y\n"),
        ("a\nb\nc\n", "@@ -3 +3 @@\n-c\n+x\n@@ -1 +1 @@\n-a\n+y\n"),
        ("a\nb\n", "@@ -1 +1 @@\n-a \n+fixed\n"),
        ("a\nb\n", "@@ -99,0 +99 @@\n+fixed\n"),
        ("a\nb\n", "@@ -1,0 +2,99 @@\n+fixed\n"),
    ],
)
def test_reject_ambiguous_overlap_reorder_changed_text_and_unanchored_insert(
    original: str, diff: str
) -> None:
    with pytest.raises(PatchError):
        apply_unified_diff(original, diff)


def test_multiple_hunks_use_original_coordinates() -> None:
    assert (
        apply_unified_diff(
            "a\nb\nc\nd\n",
            "@@ -99 +99,2 @@\n-a\n+A\n+extra\n@@ -1 +1 @@\n-d\n+D\n",
        )
        == "A\nextra\nb\nc\nD\n"
    )


def test_unanchored_insertion_keeps_explicit_address() -> None:
    assert apply_unified_diff("a\nb\n", "@@ -1,0 +2 @@\n+new\n") == "a\nnew\nb\n"


def test_reject_small_whole_file_rewrite() -> None:
    with pytest.raises(PatchError):
        apply_unified_diff("a\nb\n", "@@ -1,2 +1,2 @@\n-a\n-b\n+x\n+y\n")
