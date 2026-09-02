"""The patch tool is the single delta-oriented workspace mutation primitive."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
