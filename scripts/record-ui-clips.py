#!/usr/bin/env python
"""Record one short clip per AgentEvolver UI feature, for docs/ui.html.

Drives an already-running UI with Playwright and writes one webm per clip.
Nothing is dispatched to an agent -- these are UI tours, not task runs, so no
model credits are spent and no session does real work.

    scripts/run-in-sandbox.sh -- scripts/serve-ui.sh     # in one terminal
    python scripts/record-ui-clips.py /tmp/uiclips       # in another
    scripts/encode-ui-clips.sh /tmp/uiclips docs/assets/ui

Needs Playwright *on the host* (the sandbox image has the package but not the
browser binaries): `pip install playwright && playwright install chromium`.
Record a subset by naming clips: `... /tmp/uiclips 04-code 05-science`.

Override the target with UI_URL when the dev server is not on the default port.
"""
import os, sys, shutil, pathlib
from playwright.sync_api import sync_playwright

URL = os.environ.get("UI_URL", "http://127.0.0.1:5173")
W, H = 1600, 1000
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/uiclips")

# A synthetic pointer: Playwright's recorder does not draw the real cursor, so a
# tour without one looks like the page changing by itself.
CURSOR_JS = """
(() => {
  if (document.getElementById('__cur')) return;
  const c = document.createElement('div');
  c.id = '__cur';
  c.style.cssText = `position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;
    pointer-events:none;transition:transform .45s cubic-bezier(.4,0,.2,1);
    transform:translate(-100px,-100px);`;
  c.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22">
      <path d="M5 2l7 18 2.2-7.2L21 10.5z" fill="#111" stroke="#fff" stroke-width="1.4"/></svg>`;
  document.body.appendChild(c);
  const r = document.createElement('div');
  r.id = '__ring';
  r.style.cssText = `position:fixed;left:0;top:0;width:34px;height:34px;border-radius:50%;
    z-index:2147483646;pointer-events:none;border:2px solid rgba(59,130,246,.9);
    opacity:0;transform:translate(-100px,-100px) scale(.4);`;
  document.body.appendChild(r);
  window.__moveCur = (x, y) => {
    c.style.transform = `translate(${x - 3}px, ${y - 2}px)`;
    r.style.transform = `translate(${x - 17}px, ${y - 17}px) scale(.4)`;
  };
  window.__ripple = (x, y) => {
    r.style.transition = 'none';
    r.style.transform = `translate(${x - 17}px, ${y - 17}px) scale(.4)`;
    r.style.opacity = '1';
    requestAnimationFrame(() => {
      r.style.transition = 'transform .45s ease-out, opacity .45s ease-out';
      r.style.transform = `translate(${x - 17}px, ${y - 17}px) scale(1.25)`;
      r.style.opacity = '0';
    });
  };
})();
"""


# These clips are published to a public site, so anything that identifies the
# real lab network has to go before the recorder sees it. Runs on a
# MutationObserver because the host list re-renders as the gateway pushes state.
REDACT_JS = r"""
(() => {
  const RULES = [
    [/\b(?:10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, '10.0.0.12'],
    [/\b(?:wentao|wtzhang|jianguo|hanjun|xuanwei|yinzi|yixuan)\b/gi, 'demo'],
    [/\bgpu2\b/gi, 'gpu-node'],
  ];
  const scrub = (root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const hits = [];
    while (w.nextNode()) hits.push(w.currentNode);
    for (const n of hits) {
      let v = n.nodeValue;
      for (const [re, to] of RULES) v = v.replace(re, to);
      if (v !== n.nodeValue) n.nodeValue = v;
    }
    for (const el of root.querySelectorAll ? root.querySelectorAll('[title]') : []) {
      let v = el.getAttribute('title');
      for (const [re, to] of RULES) v = v.replace(re, to);
      el.setAttribute('title', v);
    }
  };
  scrub(document.body);
  new MutationObserver((ms) => {
    for (const m of ms) {
      if (m.target) scrub(m.target.nodeType === 1 ? m.target : document.body);
    }
  }).observe(document.body, {childList: true, subtree: true, characterData: true});
})();
"""


class Tour:
    def __init__(self, page):
        self.p = page

    def redact(self):
        self.p.evaluate(REDACT_JS)

    def cursor(self):
        self.p.evaluate(CURSOR_JS)

    def _center(self, sel_or_el, scroll=True):
        el = self.p.query_selector(sel_or_el) if isinstance(sel_or_el, str) else sel_or_el
        if not el:
            return None, None
        # The sidebar is taller than the window: half the nav sits below the fold,
        # so a pointer move to an off-screen box would land nowhere on camera.
        if scroll:
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                self.p.wait_for_timeout(450)
            except Exception:
                pass
        box = el.bounding_box()
        if not box:
            return None, el
        return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2), el

    def point(self, target, settle=700):
        """Glide the pointer onto a target without clicking."""
        pos, el = self._center(target)
        if not pos:
            return None
        self.p.evaluate("([x,y]) => window.__moveCur && window.__moveCur(x,y)", list(pos))
        self.p.wait_for_timeout(settle)
        return el

    def tap(self, target, settle=1500):
        """Glide, ripple, then really click."""
        el = self.point(target, settle=650)
        if el is None:
            return False
        pos, _ = self._center(el)
        self.p.evaluate("([x,y]) => window.__ripple && window.__ripple(x,y)", list(pos))
        self.p.wait_for_timeout(220)
        try:
            el.click(timeout=5000)
        except Exception:
            return False
        self.p.wait_for_timeout(settle)
        return True

    def by_text(self, text, scope="button"):
        for el in self.p.query_selector_all(scope):
            try:
                if el.is_visible() and " ".join(el.inner_text().split()).startswith(text):
                    return el
            except Exception:
                pass
        return None

    def tap_text(self, text, scope="button", settle=1500):
        el = self.by_text(text, scope)
        return self.tap(el, settle) if el else False

    def hold(self, ms):
        self.p.wait_for_timeout(ms)

    def scroll_into(self, sel):
        el = self.p.query_selector(sel)
        if el:
            el.scroll_into_view_if_needed()
            self.p.wait_for_timeout(600)
        return el


def clip(browser, name, fn, boot_wait=3500):
    """One clip == one context, because the video is finalised on context close."""
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(
        viewport={"width": W, "height": H},
        record_video_dir=str(d),
        record_video_size={"width": W, "height": H},
        device_scale_factor=1,
    )
    page = ctx.new_page()
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(boot_wait)
    t = Tour(page)
    t.redact()
    t.cursor()
    t.hold(500)
    try:
        fn(t, page)
    except Exception as exc:                      # a broken step must not lose the clip
        print(f"  ! {name}: {type(exc).__name__}: {exc}")
    t.hold(900)
    ctx.close()
    vids = list(d.glob("*.webm"))
    if vids:
        final = OUT / f"{name}.webm"
        shutil.move(str(vids[0]), final)
        shutil.rmtree(d, ignore_errors=True)
        print(f"  ok {name}.webm  {final.stat().st_size//1024} KB")
    else:
        print(f"  ! {name}: no video produced")


# ---------------------------------------------------------------- the clips

def c_overview(t, page):
    """Three-pane layout, live gateway connection."""
    t.point(".brand", 900)
    t.point(".new-chat", 800)
    t.point(".projects-section", 900)
    t.point(".view-nav", 900)
    t.point(".capability-nav:not(.view-nav)", 900)
    # the connection pill in the task header
    for el in page.query_selector_all("span, div"):
        try:
            if el.is_visible() and " ".join(el.inner_text().split()) == "Connected":
                t.point(el, 1400)
                break
        except Exception:
            pass
    t.hold(700)


def c_views(t, page):
    """Chat / Canvas / Code / Science."""
    for label in ("Canvas", "Code", "Science", "Chat"):
        el = None
        for b in page.query_selector_all(".view-nav button"):
            try:
                if b.is_visible() and " ".join(b.inner_text().split()) == label:
                    el = b
                    break
            except Exception:
                pass
        if el:
            t.tap(el, settle=2400)


def c_capabilities(t, page):
    """The capability catalogue: skills, tools, agents, connectors..."""
    nav = ".capability-nav:not(.view-nav) button"
    for label in ("Skills", "Tools", "Agents", "Connectors"):
        el = None
        for b in page.query_selector_all(nav):
            try:
                if b.is_visible() and " ".join(b.inner_text().split()).startswith(label):
                    el = b
                    break
            except Exception:
                pass
        if el:
            t.tap(el, settle=1900)
    # close whatever modal is open
    page.keyboard.press("Escape")
    t.hold(800)


def c_composer(t, page):
    """Starter cards and the task composer -- typed, deliberately not sent."""
    for el in page.query_selector_all("*"):
        try:
            if el.is_visible() and " ".join(el.inner_text().split()) == "Plan a feature":
                t.point(el, 1100)
                break
        except Exception:
            pass
    box = page.query_selector("textarea") or page.query_selector("[contenteditable='true']")
    if box:
        t.point(box, 700)
        box.click()
        for ch in "Trace how the Gateway routes a session event":
            page.keyboard.type(ch)
            page.wait_for_timeout(38)
        t.hold(1600)
        # leave the composer clean; nothing is dispatched
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    t.hold(500)


def c_panels(t, page):
    """Files / Activity / Inspector on the right."""
    for label in ("ACTIVITY", "INSPECTOR", "FILES"):
        el = None
        for b in page.query_selector_all("button, [role='tab'], div"):
            try:
                if b.is_visible() and " ".join(b.inner_text().split()) == label:
                    el = b
                    break
            except Exception:
                pass
        if el:
            t.tap(el, settle=2000)


def c_machines(t, page):
    """Local machines, remote machines, deployments."""
    t.scroll_into(".machines-local")
    t.point(".machines-local", 1200)
    for row in page.query_selector_all(".machines-local .host-row")[:2]:
        t.point(row, 1300)
    for sel in (".hosts-section", ".sidebar-section"):
        el = page.query_selector(sel)
        if el:
            el.scroll_into_view_if_needed()
            t.hold(700)
    t.hold(900)


def c_models(t, page):
    """Model providers."""
    el = None
    for b in page.query_selector_all(".model-nav button"):
        if b.is_visible():
            el = b
            break
    if el:
        t.tap(el, settle=2600)
        page.keyboard.press("Escape")
    t.hold(700)


def c_theme(t, page):
    """Dark / light theme, and the gateway connection settings."""
    if not t.tap_text("Dark theme", settle=2200):
        t.tap_text("Light theme", settle=2200)
    t.tap_text("Light theme", settle=1800) or t.tap_text("Dark theme", settle=1800)
    t.tap_text("Connection", settle=2400)
    page.keyboard.press("Escape")
    t.hold(700)


def _open_view(t, page, label, settle=2500):
    for b in page.query_selector_all(".view-nav button"):
        try:
            if b.is_visible() and " ".join(b.inner_text().split()) == label:
                return t.tap(b, settle=settle)
        except Exception:
            pass
    return False


def c_canvas(t, page):
    """The node editor: component palette, categories, the flow surface."""
    _open_view(t, page, "Canvas", settle=3000)
    # walk the palette the way someone hunting for a node would
    for cat in ("Input & Output", "Flow Control", "Agents", "Processing"):
        el = None
        for d in page.query_selector_all("div, button, span"):
            try:
                if d.is_visible() and " ".join(d.inner_text().split()) == cat:
                    el = d
                    break
            except Exception:
                pass
        if el:
            t.point(el, 1000)
    # then the empty flow surface it all drops onto
    for sel in (".react-flow__pane", ".react-flow", "[class*='canvas']"):
        el = page.query_selector(sel)
        if el:
            t.point(el, 1400)
            break
    t.hold(900)


def c_code(t, page):
    """The in-browser editor over the session workspace."""
    _open_view(t, page, "Code", settle=2000)
    # openvscode-server boots a container on first use; it is genuinely slow.
    frame = None
    for _ in range(40):
        page.wait_for_timeout(2000)
        for f in page.frames:
            if "/ide/" in f.url and f.url.endswith("folder=/workspace"):
                frame = f
                break
        if frame:
            break
    if frame is None:
        t.hold(1500)
        return
    page.wait_for_timeout(6000)
    # The workspace-trust modal covers the editor; it has to go before anything reads.
    for label in ("Yes, I trust the authors",):
        try:
            btn = frame.wait_for_selector(f"text={label}", timeout=15000)
            if btn:
                t.hold(900)
                btn.click()
                page.wait_for_timeout(2500)
        except Exception:
            pass
    # Close the walkthrough tab so the editor itself is what you see.
    try:
        tab = frame.query_selector(".tab.active .codicon-close, .tab .codicon-close")
        if tab:
            tab.click()
            page.wait_for_timeout(1800)
    except Exception:
        pass
    # Expand the workspace tree.
    try:
        row = frame.wait_for_selector(".explorer-folders-view .monaco-list-row", timeout=8000)
        if row:
            row.click()
            page.wait_for_timeout(1800)
    except Exception:
        pass
    t.hold(2600)


def c_science(t, page):
    """The notebook workspace: a kernel on the session's own files."""
    _open_view(t, page, "Science", settle=3000)
    # the kernel boots a Jupyter server the first time; give it a moment to settle
    page.wait_for_timeout(9000)
    for label in ("Compute", "Notebook"):
        el = None
        for b in page.query_selector_all("button, div, span"):
            try:
                if b.is_visible() and " ".join(b.inner_text().split()) == label:
                    el = b
                    break
            except Exception:
                pass
        if el:
            t.tap(el, settle=2400)
    t.hold(1200)


CLIPS = [
    ("01-overview", c_overview),
    ("02-views", c_views),
    ("03-canvas", c_canvas),
    ("04-code", c_code),
    ("05-science", c_science),
    ("03-capabilities", c_capabilities),
    ("04-composer", c_composer),
    ("05-panels", c_panels),
    ("06-machines", c_machines),
    ("07-models", c_models),
    ("08-theme", c_theme),
]


def main():
    only = sys.argv[2:] if len(sys.argv) > 2 else None
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        for name, fn in CLIPS:
            if only and name not in only:
                continue
            print(f"- recording {name} ...")
            clip(browser, name, fn)
        browser.close()


if __name__ == "__main__":
    main()
