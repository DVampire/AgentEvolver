#!/usr/bin/env python
"""Record the current workbench against a prepared, isolated sample project.

No agent task is submitted. Science runs one small Python example. Code and
Science must actually become ready; missing controls or failed steps stop the
recording instead of publishing an error screen.

    UI_URL=http://127.0.0.1:5175 UI_SESSION_ID=workspace-tour \
        python scripts/record-ui-clips.py /tmp/uiclips
    scripts/encode-ui-clips.sh /tmp/uiclips docs/assets/ui

Provide sample README.md, analysis.py and sample.csv files in that project.
Record a subset with trailing clip names, e.g. 04-code 05-science. Warm up the
IDE container before recording. Initial navigation is trimmed using manifest
marks, not guessed delays; each clip also captures an intentional poster.
"""

import json
import os
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("UI_URL", "http://127.0.0.1:5173").rstrip("/")
SESSION_ID = os.environ.get("UI_SESSION_ID", "")
W, H = 1600, 1000
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/uiclips")

CURSOR_JS = r"""
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
    z-index:2147483646;pointer-events:none;border:2px solid rgba(96,226,182,.9);
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

class Tour:
    def __init__(self, page, name):
        self.p = page
        self.name = name
        self.poster_taken = False
        page.evaluate(CURSOR_JS)

    def hold(self, ms=1000):
        self.p.wait_for_timeout(ms)

    def point(self, target, settle=650):
        el = self.p.locator(target) if isinstance(target, str) else target
        el.wait_for(state="visible")
        el.scroll_into_view_if_needed()
        box = el.bounding_box()
        assert box, f"No visible bounds: {target}"
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        self.p.evaluate("([x,y]) => window.__moveCur(x,y)", [x, y])
        self.p.mouse.move(x, y)
        self.hold(settle)
        return el

    def tap(self, target, settle=1000):
        el = self.point(target)
        box = el.bounding_box()
        self.p.evaluate("([x,y]) => window.__ripple(x,y)",
                        [box['x'] + box['width'] / 2, box['y'] + box['height'] / 2])
        el.click()
        self.hold(settle)

    def view(self, name):
        self.tap(self.p.locator('.sidebar .view-nav').get_by_role('button', name=name, exact=True))
        ready(self.p, name)

    def poster(self):
        self.p.screenshot(path=str(OUT / f'{self.name}.jpg'), type='jpeg', quality=90)
        self.poster_taken = True

    def close_dialog(self, selector):
        self.tap(self.p.locator(selector).locator('.close-dialog'))
        self.p.locator(selector).wait_for(state='hidden')


def ready(page, view):
    if view == 'Overview':
        page.get_by_text('Explore your workspace', exact=True).wait_for()
    elif view == 'Chat':
        page.locator('.composer textarea').wait_for()
        page.locator('.workspace-tree').get_by_role('treeitem', name='M↓ README.md').wait_for()
    elif view == 'Canvas':
        page.locator('.react-flow').wait_for()
        page.get_by_role('button', name='Add Chat Input', exact=True).wait_for()
    elif view == 'Code':
        page.frame_locator('iframe[title="VS Code"]').locator('.monaco-workbench').wait_for(timeout=120000)
    elif view == 'Science':
        page.locator('.kernel-panel .kernel-dot.idle').wait_for(timeout=120000)
        assert page.locator('.science-rail-notice').count() == 0


def prepare_code(page):
    """Set up the editor for our own sample files before recording begins."""
    frame = page.frame_locator('iframe[title="VS Code"]')
    frame.get_by_text('analysis.py', exact=True).first.wait_for(timeout=60000)
    page.wait_for_timeout(1500)
    trust = frame.get_by_role('button', name='Yes, I trust the authors', exact=True)
    if trust.is_visible():
        # This is the recorder's prepared project, never an arbitrary checkout.
        trust.click()
    frame.get_by_text('analysis.py', exact=True).first.click()
    frame.locator('.monaco-editor').first.wait_for()
    frame.locator('body').press('Control+k')
    frame.locator('body').press('Control+t')
    frame.get_by_role('option', name='Dark Modern, Default Dark Modern', exact=True).click()
    sidebar = frame.get_by_role('button', name='Hide Secondary Side Bar (Ctrl+Alt+B)', exact=True)
    if sidebar.is_visible():
        sidebar.click()
    page.wait_for_timeout(1500)
    notices = frame.get_by_role('button', name='Clear Notification (Delete)', exact=True)
    for _ in range(notices.count()):
        notices.first.click()
    frame.get_by_text('README.md', exact=True).first.click()
    page.wait_for_timeout(1000)
    assert not frame.locator('.monaco-dialog-modal-block').is_visible()


def c_overview(t, page):
    t.point('.overview-stats', 1400)
    t.tap(page.get_by_role('tab', name='Detailed plan', exact=True))
    t.tap(page.get_by_role('tab', name='Summary', exact=True))
    t.point('.overview-runtime', 900)
    t.poster()
    t.point('.overview-entities', 1500)
    t.tap(page.locator('.overview-entities > button').filter(has_text='Memory'))
    t.tap(page.locator('#overview-memory summary'))
    page.get_by_text('Shared workspace note', exact=True).wait_for()
    t.hold(1600)
    t.tap(page.get_by_role('button', name='Close session notes'))
    page.locator('.overview-mode').evaluate('(el) => el.scrollTo({top:0, behavior:"smooth"})')
    t.hold(1600)


def c_views(t, page):
    for name in ('Chat', 'Canvas', 'Code', 'Science', 'Overview'):
        t.view(name)
        t.hold(1300)
    t.poster()


def c_canvas(t, page):
    t.tap(page.get_by_role('button', name='Add Chat Input', exact=True))
    t.tap(page.get_by_title('Zoom', exact=True), 300)
    t.tap(page.get_by_role('menuitem', name='Zoom to 100%', exact=False), 700)
    canvas = page.locator('.react-flow').bounding_box()

    def place(node, x, y):
        head = node.locator('.lf-node-head')
        t.point(head, 400)
        box = head.bounding_box()
        page.mouse.move(box['x'] + 80, box['y'] + 20)
        page.mouse.down()
        page.mouse.move(canvas['x'] + x + 80, canvas['y'] + y + 20, steps=24)
        page.mouse.up()
        t.hold(500)

    first = page.locator('.react-flow__node').first
    place(first, 70, 130)
    first.get_by_placeholder('name', exact=True).fill('message')
    t.tap(page.get_by_role('button', name='Add Chat Output', exact=True))
    second = page.locator('.react-flow__node').last
    place(second, 590, 235)
    second.get_by_placeholder('name', exact=True).fill('result')
    source = first.locator('.react-flow__handle.source').first
    target = second.locator('.react-flow__handle.target').first
    t.point(source, 800)
    source.drag_to(target)
    page.locator('.react-flow__edge').wait_for()
    t.hold(1500)
    t.poster()
    t.point(page.locator('.canvas-cat-head').filter(has_text='Flow Control'), 1200)
    t.point(page.locator('.canvas-cat-head').filter(has_text='Agents'), 1200)
    t.hold(1000)


def c_code(t, page):
    frame = page.frame_locator('iframe[title="VS Code"]')
    file = frame.get_by_text('analysis.py', exact=True).first
    file.wait_for(timeout=60000)
    t.tap(file, 2300)
    frame.locator('.monaco-editor').first.wait_for()
    t.point(frame.locator('.monaco-editor').first, 1800)
    t.poster()
    t.tap(frame.get_by_text('README.md', exact=True).first, 2000)
    t.hold(1400)


def c_science(t, page):
    prompt = page.locator('.kernel-prompt textarea')
    t.tap(prompt, 350)
    prompt.fill("import pandas as pd; data = pd.read_csv('sample.csv'); print(data.to_string(index=False))")
    t.hold(1500)
    prompt.press('Enter')
    page.locator('.kernel-cell.user').last.wait_for(timeout=30000)
    page.locator('.kernel-outputs').last.wait_for(timeout=30000)
    assert not page.locator('.kernel-cell.failed').count()
    t.hold(2000)
    t.poster()
    t.tap(page.get_by_role('tab', name='Compute', exact=True), 1800)
    page.locator('.science-panel').wait_for()
    t.tap(page.get_by_role('tab', name='Notebook', exact=True), 1600)
    t.tap(page.get_by_role('button', name='Save .ipynb', exact=True))
    page.get_by_text('Saved to', exact=False).wait_for()
    t.hold(1200)


def c_capabilities(t, page):
    t.point('.overview-entities', 1300)
    for label in ('Tool', 'Agent'):
        t.tap(page.locator('.overview-entities > button').filter(has=page.get_by_text(label, exact=True)))
        page.locator('.capability-dialog').wait_for()
        t.hold(1700)
        if label == 'Tool':
            t.poster()
        t.close_dialog('.capability-dialog')
    t.tap(page.locator('.overview-entities > button').filter(has_text='Memory'))
    t.tap(page.locator('#overview-memory summary'))
    page.get_by_text('Shared workspace note', exact=True).wait_for()
    t.hold(1600)
    t.tap(page.get_by_role('button', name='Close session notes'))


def c_composer(t, page):
    t.tap(page.get_by_text('Plan a feature', exact=True), 700)
    box = page.locator('.composer textarea')
    box.fill('Build an interactive report from sample.csv. Start with a plan, then check the result.')
    t.hold(2000)
    t.poster()
    t.point(page.get_by_role('button', name='Attach files', exact=True), 1100)
    box.fill('')
    t.hold(900)


def c_panels(t, page):
    t.tap(page.locator('.workspace-tree').get_by_role('treeitem', name='M↓ README.md'))
    page.locator('.file-toolbar').get_by_text('README.md', exact=True).wait_for()
    t.hold(1800)
    t.poster()
    for label in ('Activity', 'Inspector', 'Files'):
        t.tap(page.locator('.workbench-tabs').get_by_role('button', name=label, exact=True), 1300)


def c_machines(t, page):
    t.point('.machines-local', 1800)
    for label in ('Open the live browser view', 'Open the live computer view'):
        t.point(page.get_by_title(label, exact=True), 1400)
    t.poster()
    t.hold(1300)


def c_models(t, page):
    t.tap(page.locator('.model-nav button'), 1400)
    page.get_by_role('heading', name='Providers & models', exact=True).wait_for()
    t.point('.provider-list', 1700)
    t.poster()
    page.locator('.provider-list').evaluate('(el)=>el.scrollBy({top:350,behavior:"smooth"})')
    t.hold(1900)
    t.close_dialog('.models-dialog')


def c_theme(t, page):
    t.tap(page.locator('.sidebar-footer').get_by_role('button', name='Light theme', exact=True), 1800)
    t.poster()
    t.tap(page.locator('.sidebar-footer').get_by_role('button', name='Dark theme', exact=True), 1600)
    t.tap(page.locator('.sidebar-footer').get_by_role('button', name='Connection', exact=True), 1500)
    page.keyboard.press('Escape')
    t.hold(1000)


CLIPS = [
    ('01-overview', 'Overview', c_overview),
    ('02-views', 'Overview', c_views),
    ('03-canvas', 'Canvas', c_canvas),
    ('04-code', 'Code', c_code),
    ('05-science', 'Science', c_science),
    ('06-capabilities', 'Overview', c_capabilities),
    ('07-composer', 'Chat', c_composer),
    ('08-panels', 'Chat', c_panels),
    ('09-machines', 'Overview', c_machines),
    ('10-models', 'Overview', c_models),
    ('11-theme', 'Overview', c_theme),
]


def clip(browser, name, view, fn):
    raw = OUT / 'raw' / name
    raw.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(viewport={'width':W,'height':H},
                                  record_video_dir=str(raw),
                                  record_video_size={'width':W,'height':H},
                                  color_scheme='dark',
                                  device_scale_factor=1)
    context.add_init_script('localStorage.setItem("agentevolver.gateway.session",'+json.dumps(SESSION_ID)+');localStorage.setItem("agentevolver.theme","dark");')
    page = context.new_page()
    started = time.monotonic()
    page.set_default_timeout(20000)
    try:
        page.goto(URL, wait_until='networkidle', timeout=60000)
        ready(page, 'Overview')
        if name in ('02-views', '04-code'):
            page.locator('.sidebar .view-nav').get_by_role('button', name='Code', exact=True).click()
            ready(page, 'Code')
            prepare_code(page)
            if view != 'Code':
                page.locator('.sidebar .view-nav').get_by_role('button', name=view, exact=True).click()
                ready(page, view)
        elif view != 'Overview':
            page.locator('.sidebar .view-nav').get_by_role('button', name=view, exact=True).click()
            ready(page, view)
        page.evaluate('document.fonts.ready')
        page.wait_for_timeout(1000)
        t = Tour(page, name)
        begin = time.monotonic() - started
        t.hold(900)
        fn(t, page)
        t.hold(1200)
        if not t.poster_taken:
            t.poster()
        duration = time.monotonic() - started - begin
        assert not page.get_by_text('The IDE could not start', exact=True).count()
        video = page.video
        context.close()
        final = OUT / f'{name}.webm'
        video.save_as(str(final))
        video.delete()
        return {'start_seconds':round(begin,3),'duration_seconds':round(duration,3),
                'width':W,'height':H,'source_url':URL,'session_id':SESSION_ID}
    except Exception:
        page.screenshot(path=str(OUT/f'{name}.failed.png'))
        context.close()
        raise


def main():
    if not SESSION_ID:
        raise SystemExit('Set UI_SESSION_ID to a prepared, isolated sample project before recording.')
    requested = sys.argv[2:]
    unknown = set(requested) - {name for name, _, _ in CLIPS}
    if unknown:
        raise SystemExit(f'Unknown clips: {sorted(unknown)}')
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT/'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--force-color-profile=srgb','--no-sandbox'])
        try:
            for name, view, fn in CLIPS:
                if requested and name not in requested:
                    continue
                print(f'Recording {name}…', flush=True)
                manifest.pop(name, None)
                manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
                manifest[name] = clip(browser, name, view, fn)
                manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
                print(f'Recorded {name}: {manifest[name]["duration_seconds"]:.1f}s', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
