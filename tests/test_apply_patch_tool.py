"""The patch tool is the single delta-oriented workspace mutation primitive."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.tool.default.workspace.apply_patch import ApplyPatchTool


def _ctx(root):
    return SimpleNamespace(extra={"execution_cwd": str(root)})


def _call(root, patch):
    return asyncio.run(ApplyPatchTool()(patch=patch, ctx=_ctx(root)))


def test_apply_patch_updates_one_file(tmp_path):
    target = tmp_path / "app.js"
    target.write_text('console.log("old");\n', encoding="utf-8")
    patch = """diff --git a/app.js b/app.js
--- a/app.js
+++ b/app.js
@@ -1 +1 @@
-console.log("old");
+console.log("new");
"""

    response = _call(tmp_path, patch)

    assert response.success, response.message
    assert target.read_text(encoding="utf-8") == 'console.log("new");\n'
    assert response.data["additions"] == 1
    assert response.data["deletions"] == 1


def test_apply_patch_creates_a_small_file_outside_a_git_repo(tmp_path):
    patch = """diff --git a/src/main.py b/src/main.py
new file mode 100644
--- /dev/null
+++ b/src/main.py
@@ -0,0 +1 @@
+print("ok")
"""

    response = _call(tmp_path, patch)

    assert response.success, response.message
    assert (tmp_path / "src" / "main.py").read_text(encoding="utf-8") == 'print("ok")\n'


def test_apply_patch_accepts_classic_unified_diff(tmp_path):
    patch = """--- /dev/null
+++ b/plan.md
@@ -0,0 +1,2 @@
+# Plan
+Ship the smallest verified product.
"""

    response = _call(tmp_path, patch)

    assert response.success, response.message
    assert (tmp_path / "plan.md").read_text(encoding="utf-8") == (
        "# Plan\nShip the smallest verified product.\n"
    )


def test_apply_patch_rejects_bare_hunk_header_without_false_success(tmp_path):
    patch = """diff --git a/plan.md b/plan.md
new file mode 100644
--- /dev/null
+++ b/plan.md
@@
+# Plan
"""

    response = _call(tmp_path, patch)

    assert not response.success
    assert "invalid hunk header" in response.message
    assert not (tmp_path / "plan.md").exists()


def test_apply_patch_rejects_stale_context_without_mutating(tmp_path):
    target = tmp_path / "app.js"
    target.write_text("current\n", encoding="utf-8")
    patch = """diff --git a/app.js b/app.js
--- a/app.js
+++ b/app.js
@@ -1 +1 @@
-stale
+replacement
"""

    response = _call(tmp_path, patch)

    assert not response.success
    assert "workspace unchanged" in response.message
    assert target.read_text(encoding="utf-8") == "current\n"


def test_apply_patch_rejects_multiple_files(tmp_path):
    patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -0,0 +1 @@
+a
diff --git a/b.txt b/b.txt
--- a/b.txt
+++ b/b.txt
@@ -0,0 +1 @@
+b
"""

    response = _call(tmp_path, patch)

    assert not response.success
    assert "exactly one" in response.message
    assert list(tmp_path.iterdir()) == []


def test_apply_patch_rejects_workspace_escape(tmp_path):
    patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -0,0 +1 @@
+no
"""

    response = _call(tmp_path, patch)

    assert not response.success
    assert "unsafe patch path" in response.message


@pytest.mark.parametrize("prefix", ["", "b/"])
def test_nested_creation_uses_the_declared_target(tmp_path, prefix):
    patch = f"--- /dev/null\n+++ {prefix}app/index.html\n@@ -0,0 +1 @@\n+<main>ECHO</main>\n"
    response = _call(tmp_path, patch)
    assert response.success, response.message
    assert (tmp_path / "app/index.html").read_text() == "<main>ECHO</main>\n"
    assert not (tmp_path / "index.html").exists()
    assert response.files == [str(tmp_path / "app/index.html")]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_classic_nested_patch_does_not_touch_same_named_root_file(tmp_path, operation):
    target = tmp_path / "app/index.html"
    target.parent.mkdir()
    target.write_text("old\n")
    decoy = tmp_path / "index.html"
    decoy.write_text("old\n")
    patch = ("--- app/index.html\n+++ app/index.html\n@@ -1 +1 @@\n-old\n+new\n"
             if operation == "update" else
             "--- app/index.html\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n")
    response = _call(tmp_path, patch)
    assert response.success, response.message
    assert decoy.read_text() == "old\n"
    if operation == "update":
        assert target.read_text() == "new\n"
    else:
        assert not target.exists()
