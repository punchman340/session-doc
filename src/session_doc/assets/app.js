/*
 * session-doc viewer script.
 *
 * Pages work without it: items are selected with #fragment links and :target. This
 * script builds the session sidebar and runs search, both from the per-project data
 * file (window.sessionDocData). Text from logs is only ever set with textContent;
 * nothing is parsed as HTML. It makes no network requests.
 */
(function () {
  "use strict";

  const RESULT_LIMIT = 100;
  const data = window.sessionDocData;
  const body = document.body;
  const sessionsBase = body.dataset.sessions || "";
  const currentSession = body.dataset.session || "";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function sessionHref(slug, anchor) {
    return sessionsBase + encodeURIComponent(slug) + ".html" + (anchor ? "#" + anchor : "");
  }

  function badges(kind, inferred) {
    const frag = document.createDocumentFragment();
    frag.appendChild(el("span", "badge kind-" + kind, kind));
    if (inferred) {
      const mark = el("span", "inferred", "inferred");
      mark.title = "Found by a weak signal";
      frag.appendChild(mark);
    }
    return frag;
  }

  // Scroll `node` into view inside its own pane only, never the whole page.
  function reveal(node, pane) {
    if (!node || !pane) return;
    const n = node.getBoundingClientRect();
    const p = pane.getBoundingClientRect();
    if (n.top < p.top || n.bottom > p.bottom) {
      pane.scrollTop += n.top - p.top - p.height / 3;
    }
  }

  // ------------------------------------------------------------------ sidebar

  function buildSidebar() {
    const list = document.getElementById("session-list");
    if (!list || !data || !Array.isArray(data.sessions)) return;
    const frag = document.createDocumentFragment();
    let active = null;
    for (const s of data.sessions) {
      const li = el("li");
      const a = el("a");
      a.href = sessionHref(s.slug);
      a.appendChild(el("span", "session-title", s.title));
      a.appendChild(el("span", "session-date", s.date));
      a.title = s.title;
      if (s.slug === currentSession) {
        a.setAttribute("aria-current", "page");
        active = a;
      }
      li.appendChild(a);
      frag.appendChild(li);
    }
    list.replaceChildren(frag);
    reveal(active, list.closest(".sidebar"));
  }

  // ------------------------------------------------------------------ current item

  function markCurrentItem() {
    let active = null;
    for (const a of document.querySelectorAll(".items a[href^='#']")) {
      if (a.getAttribute("href") === location.hash) {
        a.setAttribute("aria-current", "true");
        active = a;
      } else {
        a.removeAttribute("aria-current");
      }
    }
    reveal(active, document.querySelector(".list-pane"));
  }

  // ------------------------------------------------------------------ search

  function snippet(text, terms) {
    const lower = text.toLowerCase();
    let at = -1;
    for (const term of terms) {
      const i = lower.indexOf(term);
      if (i >= 0 && (at < 0 || i < at)) at = i;
    }
    const start = Math.max(0, at - 60);
    const end = Math.min(text.length, Math.max(at, 0) + 120);
    const part = text.slice(start, end).replace(/\s+/g, " ").trim();
    return (start > 0 ? "..." : "") + part + (end < text.length ? "..." : "");
  }

  function setupSearch() {
    const box = document.getElementById("search");
    const results = document.getElementById("results");
    const content = document.getElementById("content");
    if (!box || !results || !content) return;
    if (!data || !Array.isArray(data.docs) || typeof window.MiniSearch !== "function") return;

    const sessions = new Map((data.sessions || []).map((s) => [s.slug, s]));
    const docs = new Map(data.docs.map((d) => [d.id, d]));
    let index = null;
    let timer = 0;

    function ensureIndex() {
      if (!index) {
        index = new window.MiniSearch({
          fields: ["title", "text"],
          searchOptions: { prefix: true, boost: { title: 2 }, combineWith: "AND" },
        });
        index.addAll(data.docs);
      }
      return index;
    }

    function show(found) {
      const frag = document.createDocumentFragment();
      let label = found.length === 1 ? "1 result" : found.length + " results";
      if (found.length === 0) label = "No results";
      if (found.length > RESULT_LIMIT) label += ", showing the first " + RESULT_LIMIT;
      frag.appendChild(el("p", "result-count", label));
      const list = el("ol", "items");
      for (const hit of found.slice(0, RESULT_LIMIT)) {
        const doc = docs.get(hit.id);
        if (!doc) continue;
        const session = sessions.get(doc.session);
        const a = el("a");
        a.href = sessionHref(doc.session, doc.anchor);
        const meta = el("span", "item-meta");
        meta.appendChild(badges(doc.kind, doc.inferred));
        a.appendChild(meta);
        a.appendChild(el("span", "item-title", doc.title));
        if (session && session.slug !== currentSession) {
          a.appendChild(el("span", "result-session", session.title + " - " + session.date));
        }
        a.appendChild(el("span", "snippet", snippet(doc.text, hit.terms)));
        const li = el("li");
        li.appendChild(a);
        list.appendChild(li);
      }
      frag.appendChild(list);
      results.replaceChildren(frag);
      results.hidden = false;
      content.hidden = true;
    }

    function run() {
      const query = box.value.trim();
      if (!query) {
        results.hidden = true;
        results.replaceChildren();
        content.hidden = false;
        return;
      }
      show(ensureIndex().search(query));
    }

    box.addEventListener("focus", ensureIndex, { once: true });
    box.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 120);
    });
    box.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        box.value = "";
        run();
      }
    });
    document.addEventListener("keydown", (event) => {
      const target = event.target;
      const typing =
        target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
      if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        box.focus();
      }
    });
    box.closest(".search").hidden = false;
  }

  buildSidebar();
  markCurrentItem();
  window.addEventListener("hashchange", markCurrentItem);
  setupSearch();
})();
