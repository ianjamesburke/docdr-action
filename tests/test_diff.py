from docdr.diff import parse_diff, filter_diff, FileDiff

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 def hello():
+    # new comment
     return "hello"
diff --git a/package-lock.json b/package-lock.json
index 111..222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,2 +1,3 @@
 {}
diff --git a/dist/bundle.min.js b/dist/bundle.min.js
index aaa..bbb 100644
--- a/dist/bundle.min.js
+++ b/dist/bundle.min.js
@@ -1 +1 @@
-old
+new
"""


def test_parse_diff_finds_all_files():
    diffs = parse_diff(SAMPLE_DIFF)
    paths = [d.path for d in diffs]
    assert "src/main.py" in paths
    assert "package-lock.json" in paths
    assert "dist/bundle.min.js" in paths
    assert len(diffs) == 3


def test_filter_removes_lockfiles():
    diffs = parse_diff(SAMPLE_DIFF)
    filtered = filter_diff(diffs)
    paths = [d.path for d in filtered]
    assert "package-lock.json" not in paths


def test_filter_removes_minified():
    diffs = parse_diff(SAMPLE_DIFF)
    filtered = filter_diff(diffs)
    paths = [d.path for d in filtered]
    assert "dist/bundle.min.js" not in paths


def test_filter_keeps_source():
    diffs = parse_diff(SAMPLE_DIFF)
    filtered = filter_diff(diffs)
    paths = [d.path for d in filtered]
    assert "src/main.py" in paths


def test_parse_diff_captures_content():
    diffs = parse_diff(SAMPLE_DIFF)
    main_diff = next(d for d in diffs if d.path == "src/main.py")
    assert "+ # new comment" in main_diff.diff or "+    # new comment" in main_diff.diff
