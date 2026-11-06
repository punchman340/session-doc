import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from session_doc import render
from session_doc.extractor import Item, extract_items, session_title
from session_doc.parser import parse_session
from session_doc.render import (
    ProjectInfo,
    SessionInfo,
    project_data_js,
    read_project_docs,
    render_markdown,
    render_session_page,
    search_docs,
)

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(render.__file__).parent / "templates"
ASSETS = Path(render.__file__).parent / "assets"


class Tags(HTMLParser):
    """Collect start tags with their attributes, and the text content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: v or "" for k, v in attrs}))

    def handle_data(self, data):
        self.text.append(data)


def parse_html(html: str) -> Tags:
    tags = Tags()
    tags.feed(html)
    return tags


def hrefs(html: str) -> list[str]:
    return [a["href"] for t, a in parse_html(html).tags if t == "a" and "href" in a]


# ------------------------------------------------------------------------- markdown


def test_raw_html_in_markdown_is_escaped():
    out = render_markdown(
        '<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n<div>x</div>'
    )
    assert "<script" not in out and "<img" not in out and "<div" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in out


@pytest.mark.parametrize(
    "source",
    [
        "[a](javascript:alert(1))",
        "[a](JavaScript:alert(1))",
        "<javascript:alert(1)>",
        "[a](vbscript:msgbox(1))",
        "[a](data:text/html;base64,PHNjcmlwdD4=)",
        "[a](file:///etc/passwd)",
        "[a](docs/notes.md)",
        "[a](//example.com/x)",
        "[a](#item-1)",
    ],
)
def test_only_http_https_mailto_links_are_live(source):
    out = render_markdown(source)
    assert hrefs(out) == []
    assert "<a" not in out


def test_web_links_get_rel_noopener():
    out = render_markdown("[a](https://example.com/x?a=1&b=2) <mailto:me@example.com>")
    tags = [a for t, a in parse_html(out).tags if t == "a"]
    assert [a["href"] for a in tags] == ["https://example.com/x?a=1&b=2", "mailto:me@example.com"]
    assert all(a["rel"] == "noopener noreferrer" for a in tags)


def test_images_are_never_loaded():
    out = render_markdown("![pixel](https://example.com/p.png) ![x](data:image/png;base64,AAAA)")
    assert "<img" not in out
    assert hrefs(out) == ["https://example.com/p.png"]
    assert "image: pixel" in out


def test_table_alignment_uses_classes_not_style():
    out = render_markdown("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |")
    assert "style=" not in out
    assert 'class="align-left"' in out and 'class="align-center"' in out
    assert 'class="align-right"' in out


def test_code_fence_info_string_is_escaped():
    out = render_markdown('```py" onmouseover="x\nprint(1)\n```')
    assert 'onmouseover="' not in out


def test_breaks_option_keeps_single_line_breaks():
    assert "<br>" in render_markdown("one\ntwo", breaks=True)
    assert "<br>" not in render_markdown("one\ntwo")


def test_only_deliverables_are_rendered_without_breaks():
    def item(kind):
        return Item(kind, None, "t", "one\ntwo", "explicit")

    assert "<br>" not in render.item_html(item("deliverable"))
    for kind in ("prompt", "decision", "status"):
        assert "<br>" in render.item_html(item(kind))


# ------------------------------------------------------------------------- templates


def test_autoescape_is_on_and_only_markdown_is_marked_safe():
    assert render._env.autoescape is True
    uses = []
    for path in TEMPLATES.glob("*.html"):
        uses += re.findall(r"\{\{[^}]*\|\s*safe[^}]*\}\}", path.read_text(encoding="utf-8"))
    assert uses == ["{{ item.html|safe }}"]
    assert "Markup" not in Path(render.__file__).read_text(encoding="utf-8")


def _injection_page() -> str:
    session = parse_session(FIXTURES / "html_injection.jsonl")
    items = extract_items(session)
    project = ProjectInfo("demo", "a&b <i>app", session.cwd or "")
    info = SessionInfo(
        slug="html_injection",
        title=session_title(session),
        start=session.start_time,
        end=session.end_time,
        branch=session.git_branch,
        counts={"prompt": 1},
    )
    return render_session_page(project, info, items)


def test_log_with_script_and_raw_html_renders_as_text():
    html = _injection_page()
    parsed = parse_html(html)
    names = [t for t, _ in parsed.tags]
    scripts = [a.get("src") for t, a in parsed.tags if t == "script"]
    assert scripts == [
        "../search-index.js",
        "../../../assets/minisearch.min.js",
        "../../../assets/app.js",
    ]
    for banned in ("img", "iframe", "object", "embed", "style", "form", "base"):
        assert banned not in names, banned
    assert not [a for _, a in parsed.tags if any(k.startswith("on") for k in a)]
    text = "".join(parsed.text)
    # Title, branch, project name, prompt, answer, commit, and question all show as text.
    assert "<script>window.__pwned=1</script>Title & <b>bold</b>" in text
    assert "feat/<x>" in text and "a&b <i>app" in text
    assert '<img src=x onerror="window.__pwned=1">' in text
    assert "<b>Which</b> one?" in text
    assert "fix: <script>window.__pwned=1</script>" in text


def test_javascript_links_in_a_log_are_not_live():
    links = hrefs(_injection_page())
    page_links = {"../../../index.html", "../index.html"}
    content = [h for h in links if h not in page_links and not h.startswith("#item-")]
    assert content == ["https://example.com/page?a=1&b=2", "https://example.com/pixel.png"]
    assert not [h for h in links if h.lower().startswith(("javascript:", "data:", "vbscript:"))]


def test_session_page_shows_one_article_per_item_with_anchor():
    session = parse_session(FIXTURES / "basic_session.jsonl")
    items = extract_items(session)
    info = SessionInfo("s", "t", None, None, None, {})
    html = render_session_page(ProjectInfo("p", "p", "/w"), info, items)
    ids = [a["id"] for t, a in parse_html(html).tags if t == "article"]
    assert ids == [f"item-{n}" for n in range(1, len(items) + 1)]
    assert [h for h in hrefs(html) if h.startswith("#")] == [f"#{i}" for i in ids]


def test_inferred_items_are_marked():
    items = [Item("deliverable", None, "Long answer", "x", "inferred")]
    html = render_session_page(
        ProjectInfo("p", "p", "/w"), SessionInfo("s", "t", None, None, None, {}), items
    )
    assert html.count('class="inferred"') == 2  # item list and reader


# ------------------------------------------------------------------------- data file


def test_project_data_js_is_ascii_and_cannot_close_a_script():
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    title = "</script><script>x()</script> & \u2028\u00a0"
    item = Item("prompt", when, title, title, "explicit")
    js = project_data_js(
        ProjectInfo("p", "p", "/w"),
        [SessionInfo("s", title, when, when, None, {"prompt": 1})],
        search_docs("s", [item]),
    )
    assert js.isascii()
    assert "<" not in js and ">" not in js and "\u2028" not in js
    payload = json.loads(js[len(render.DATA_PREFIX) : -len(render.DATA_SUFFIX)])
    assert payload["sessions"][0]["title"] == title
    assert payload["docs"][0]["text"] == title


def test_read_project_docs_round_trips():
    items = [Item("status", None, "fix: x", "fix: x", "explicit")]
    docs = search_docs("s1", items) + search_docs("s2", items)
    js = project_data_js(ProjectInfo("p", "p", "/w"), [], docs)
    found = read_project_docs(js)
    assert found is not None
    assert sorted(found) == ["s1", "s2"]
    assert found["s1"][0] == {
        "id": "s1/item-1",
        "session": "s1",
        "anchor": "item-1",
        "kind": "status",
        "inferred": False,
        "title": "fix: x",
        "text": "fix: x",
    }


@pytest.mark.parametrize(
    "text",
    ["", "window.sessionDocData = {};\n", render.DATA_PREFIX + "{not json" + render.DATA_SUFFIX],
)
def test_read_project_docs_rejects_other_files(text):
    assert read_project_docs(text) is None


def test_read_project_docs_rejects_other_formats():
    js = project_data_js(ProjectInfo("p", "p", "/w"), [], [])
    assert read_project_docs(js.replace('"format":1', '"format":999')) is None


# ------------------------------------------------------------------------- assets


def test_asset_files_are_ascii():
    files = render.asset_files()
    assert sorted(files) == sorted(render.ASSETS)
    for name, data in files.items():
        assert data.isascii(), name


def test_viewer_script_builds_no_html_and_makes_no_requests():
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "sendBeacon",
        "importScripts",
    ):
        assert banned not in js, banned


def test_vendored_minisearch_is_pinned_and_licensed():
    js = (ASSETS / "minisearch.min.js").read_text(encoding="utf-8")
    assert js.startswith("/*! MiniSearch 7.2.0 ")
    for banned in ("XMLHttpRequest", "WebSocket", "sendBeacon", "importScripts"):
        assert banned not in js, banned
    license_text = (ASSETS / "minisearch.LICENSE.txt").read_text(encoding="utf-8")
    assert "Luca Ongaro" in license_text and "Permission is hereby granted" in license_text
