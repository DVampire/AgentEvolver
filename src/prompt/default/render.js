(function () {
  // Minimal Markdown renderer — handles only what the prompt files use:
  // **bold**, *italic*, `code`, - lists (nested), > blockquote
  // Leaves <pre> blocks and Jinja {{ }} untouched.

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function inlineMarkdown(text) {
    // Protect existing HTML tags from double-processing
    return text
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+?)`/g, "<code>$1</code>");
  }

  function parseLines(lines) {
    var html = "";
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      // Blank line
      if (line.trim() === "") {
        i++;
        continue;
      }

      // Bullet list item (top-level or nested)
      if (/^(\s*)- /.test(line)) {
        var result = parseList(lines, i, 0);
        html += result.html;
        i = result.next;
        continue;
      }

      // Numbered list item
      if (/^\d+\. /.test(line)) {
        var result = parseOrderedList(lines, i);
        html += result.html;
        i = result.next;
        continue;
      }

      // Plain paragraph line
      html += "<p>" + inlineMarkdown(line.trim()) + "</p>\n";
      i++;
    }

    return html;
  }

  function getIndent(line) {
    var m = line.match(/^(\s*)/);
    return m ? m[1].length : 0;
  }

  function parseList(lines, start, baseIndent) {
    var html = "<ul>\n";
    var i = start;

    while (i < lines.length) {
      var line = lines[i];
      if (line.trim() === "") { i++; continue; }

      var indent = getIndent(line);
      var isBullet = /^\s*- /.test(line);

      if (!isBullet || indent < baseIndent) break;

      if (indent === baseIndent) {
        var content = line.replace(/^\s*- /, "");
        // Peek: next line a nested list?
        var j = i + 1;
        while (j < lines.length && lines[j].trim() === "") j++;
        if (j < lines.length && /^\s*- /.test(lines[j]) && getIndent(lines[j]) > baseIndent) {
          html += "<li>" + inlineMarkdown(content.trim()) + "\n";
          var nested = parseList(lines, j, getIndent(lines[j]));
          html += nested.html;
          html += "</li>\n";
          i = nested.next;
        } else {
          html += "<li>" + inlineMarkdown(content.trim()) + "</li>\n";
          i++;
        }
      } else {
        // deeper indent — shouldn't reach here normally
        break;
      }
    }

    html += "</ul>\n";
    return { html: html, next: i };
  }

  function parseOrderedList(lines, start) {
    var html = "<ol>\n";
    var i = start;
    while (i < lines.length) {
      var line = lines[i];
      if (!/^\d+\. /.test(line)) break;
      var content = line.replace(/^\d+\. /, "");
      html += "<li>" + inlineMarkdown(content.trim()) + "</li>\n";
      i++;
    }
    html += "</ol>\n";
    return { html: html, next: i };
  }

  function renderNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      var text = node.textContent;
      if (!/\*\*|\*|`|^- /m.test(text)) return; // nothing to do

      // Split text by lines and parse
      var lines = text.split("\n");
      var rendered = parseLines(lines);
      if (rendered.trim() === "") return;

      var wrapper = document.createElement("span");
      wrapper.innerHTML = rendered;
      node.parentNode.replaceChild(wrapper, node);
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) return;

    // Skip pre/code blocks — don't touch their content
    var tag = node.tagName.toLowerCase();
    if (tag === "pre" || tag === "code") return;

    // Recurse on children (snapshot first because we may mutate)
    var children = Array.prototype.slice.call(node.childNodes);
    children.forEach(renderNode);
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderNode(document.body);
  });
})();
