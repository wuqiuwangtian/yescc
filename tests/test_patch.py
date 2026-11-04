"""unified diff 严格应用测试。"""

from __future__ import annotations

import pytest

from yescc.patch import PatchError, apply_unified_diff

ORIGINAL = "#include <stdio.h>\nint main(void) { printf(\"hi\\n\") return 0; }\n"

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
        f"-int value_{index} = {index};\n+int value_{index} = {index + 1};\n"
        for index in range(20)
    )
    wrapped = f"--- a/main.c\n+++ b/main.c\n@@ -1,20 +1,20 @@\n{diff}"
    with pytest.raises(PatchError):
        apply_unified_diff(original, wrapped)
