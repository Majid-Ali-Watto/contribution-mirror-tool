"use strict";
// The page for `mirror.py ui`. Every call carries the session token handed over in the link's #fragment (never sent
// to the server as part of a URL, never in a referrer). Everything from the server is inserted as text, not HTML.

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const TABS = ["setup", "sync", "accounts", "schedule", "logs"];
const START_TAB = (location.hash.match(/(?:^#|&)tab=(setup|sync|accounts|schedule|logs)/) || [])[1];

// The session token: from the terminal's link (#t=), or traded once for the one-time code the browser was opened with
// (#c=), then kept for this tab only. Either way the fragment is removed from the address bar and history at once.
const SESSION = (async () => {
  const t = location.hash.match(/(?:^#|&)t=([^&]+)/);
  const c = location.hash.match(/(?:^#|&)c=([^&]+)/);
  if (t || c) history.replaceState(null, "", location.pathname);
  let token = t ? decodeURIComponent(t[1]) : "";
  if (!token && c) {
    const res = await fetch("/api/session", { method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: decodeURIComponent(c[1]) }) });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    token = data.token;
  }
  try {
    if (token) sessionStorage.setItem("mirror-token", token);
    else token = sessionStorage.getItem("mirror-token") || "";
  } catch (_) { /* storage may be blocked: the link still works for this load */ }
  return token;
})();

async function api(path, body) {
  const opts = { headers: { "X-Mirror-Token": await SESSION }, cache: "no-store" };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (_) {
    throw new Error(`The page lost its connection to the tool. Is \`${(state && state.command) || "mirror.py"} ui\` still running?`);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

const show = (node, on) => { node.hidden = !on; };
function flash(node) {
  show(node, true);
  clearTimeout(node._t);
  node._t = setTimeout(() => show(node, false), 2500);
}
function banner(node, text) {
  node.textContent = text || "";
  show(node, !!text);
}

// ------------------------------------------------------------------------------------------------ tooltips
// Every control explains itself in its `title`. A browser shows a title only on mouse hover, so the text is moved to
// data-tip and shown here instead: on hover, on keyboard focus, and on a long press (touch screens). While it is
// shown, the control is described by it for screen readers. Works for controls the page creates later too.
const tip = el("div", "tip");
tip.id = "tip";
tip.setAttribute("role", "tooltip");
tip.hidden = true;
document.body.append(tip);
let tipFor = null, tipAnchor = null, tipTimer = 0, pressTimer = 0, longPressed = false;

function tipTarget(node) {
  const t = node instanceof Element ? node.closest("[title], [data-tip]") : null;
  if (!t || t === document.documentElement) return null;
  if (t.hasAttribute("title")) {                  // take it over, or the browser would show its own as well
    if (t.title) t.dataset.tip = t.title;
    t.removeAttribute("title");
  }
  return t.dataset.tip ? t : null;
}

function showTip(t, described = t) {
  clearTimeout(tipTimer);
  if (tipFor && tipFor !== described) tipFor.removeAttribute("aria-describedby");
  tipFor = described;
  tipAnchor = t;
  described.setAttribute("aria-describedby", "tip");
  tip.textContent = t.dataset.tip;
  tip.hidden = false;
  placeTip();
}

function placeTip() {                              // next to its control, inside the window
  if (!tipAnchor || tip.hidden) return;
  const r = tipAnchor.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), vw - w - 8);
  const top = r.bottom + 8 + h <= vh - 8 ? r.bottom + 8 : Math.max(8, r.top - h - 8);
  tip.style.left = Math.round(left) + "px";
  tip.style.top = Math.round(top) + "px";
}

function hideTip() {
  clearTimeout(tipTimer);
  clearTimeout(pressTimer);
  if (tipFor) tipFor.removeAttribute("aria-describedby");
  tipFor = tipAnchor = null;
  tip.hidden = true;
}

document.addEventListener("pointerover", (ev) => {
  if (ev.pointerType !== "mouse") return;
  const t = tipTarget(ev.target);
  if (!t) return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => showTip(t), 400);
});
document.addEventListener("pointerout", (ev) => {
  if (ev.pointerType !== "mouse") return;
  const t = ev.target instanceof Element ? ev.target.closest("[data-tip]") : null;
  if (t && !(ev.relatedTarget instanceof Node && t.contains(ev.relatedTarget))) hideTip();
});
document.addEventListener("focusin", (ev) => {
  const t = tipTarget(ev.target);
  if (t && ev.target.matches(":focus-visible")) showTip(t, ev.target);   // keyboard focus, not a click
});
document.addEventListener("focusout", hideTip);
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !tip.hidden) hideTip(); });
// focusing a control may scroll it into view: the tip follows instead of disappearing
window.addEventListener("scroll", placeTip, { passive: true, capture: true });
window.addEventListener("resize", placeTip);

// touch: hold a control for half a second to read what it does; that press does not also click it
document.addEventListener("pointerdown", (ev) => {
  if (ev.pointerType === "mouse") { hideTip(); return; }
  const t = tipTarget(ev.target);
  longPressed = false;
  if (!tip.hidden) hideTip();
  if (t) pressTimer = setTimeout(() => { longPressed = true; showTip(t); }, 500);
});
for (const type of ["pointerup", "pointercancel", "pointermove"]) {
  document.addEventListener(type, (ev) => {
    if (ev.pointerType !== "mouse" && (type !== "pointermove" || !longPressed)) clearTimeout(pressTimer);
  });
}
document.addEventListener("click", (ev) => {
  if (longPressed) { ev.preventDefault(); ev.stopPropagation(); longPressed = false; }
}, true);
document.addEventListener("contextmenu", (ev) => { if (longPressed) ev.preventDefault(); });

// ------------------------------------------------------------------------------------------------ state
let state = null;
let entries = [];               // the project list as edited on the page
let paused = new Set();         // the entries runs skip until resumed
let statuses = new Map();       // entry -> {ok, detail, kind} from the last save/load
let settingsDirty = false, reposDirty = false;

async function load() {
  try {
    state = await api("/api/state");
  } catch (e) {
    banner($("#fatal"), e.message);
    setStatus("err", "Not connected");
    return;
  }
  banner($("#fatal"), "");
  fillProfiles();
  $("#version").textContent = state.version ? "v" + state.version : "";
  $("#env-path").textContent = state.paths.env;
  $("#repos-path").textContent = state.paths.repos;
  if (!settingsDirty) fillSettings(state.settings);
  if (!reposDirty) takeRepos(state.repos);
  show($("#repos-legacy"), state.repos_legacy);
  banner($("#repos-error"), state.repos_error);
  banner($("#overridden"), state.overridden.length
    ? `Set in the environment, so they override this file: ${state.overridden.join(", ")}.` : "");
  $("#tokens").textContent = state.tokens.length
    ? `Tokens set in the file or environment: ${state.tokens.join(", ")} (values are never shown here).`
    : "No WORK_*TOKEN is set: logins already on this computer are used (see docs/authentication.md).";
  $("#sync-remote").textContent = state.settings.EXPECTED_REMOTE || "your mirror repo";
  updateReady();
  if (state.job) resumeJob(state.job);
}

function setStatus(kind, text) {
  const s = $("#status");
  s.className = "pill " + kind;
  s.textContent = text;
}

function updateReady() {
  if (job && job.running) return;
  const problems = [];
  if (state.missing.length) problems.push("fill in and save step 1 (" + state.missing.map(label).join(", ") + ")");
  if (!state.repos.length) problems.push("add and save at least one project in step 2");
  if (settingsDirty || reposDirty) problems.push("save your changes in Setup");
  banner($("#not-ready"), problems.length ? "Before running: " + problems.join("; ") + "." : "");
  $("#run-preview").disabled = $("#run-sync").disabled = problems.length > 0;
  if (state.ready) setStatus("ok", "Ready");
  else setStatus("warn", "Setup needed");
}

const LABELS = { WORK_EMAIL: "work email", PERSONAL_NAME: "your name", PERSONAL_EMAIL: "personal email",
                 EXPECTED_REMOTE: "mirror repo" };
const label = (k) => LABELS[k] || k;

// ------------------------------------------------------------------------------------------------ profiles
const NEW_PROFILE = "__new__";                // the "New profile…" choice (a profile name cannot start with _)

function fillProfiles() {
  const sel = $("#profile");
  sel.replaceChildren();
  for (const name of state.profiles) sel.append(new Option(name || "Default", name, false, name === state.profile));
  sel.append(new Option("New profile…", NEW_PROFILE));
}

$("#profile").addEventListener("change", async (ev) => {
  let name = ev.target.value;
  if (name === NEW_PROFILE) {
    name = (window.prompt("Name of the new profile, e.g. the employer (letters, digits, - and _):") || "").trim();
    if (!name) { fillProfiles(); return; }
  }
  if ((settingsDirty || reposDirty) && !window.confirm("Switch profiles and lose the unsaved changes in Setup?")) {
    fillProfiles();
    return;
  }
  try {
    state = await api("/api/profile", { name });
  } catch (e) {
    banner($("#fatal"), e.message);
    fillProfiles();
    return;
  }
  setSettingsDirty(false);
  setReposDirty(false);
  for (const id of ["#acc-table", "#doc-list"]) show($(id), false);
  for (const id of ["#acc-state", "#doc-state"]) $(id).textContent = "";
  await load();
});

// ------------------------------------------------------------------------------------------------ setup: you
function fillSettings(s) {
  const f = $("#settings");
  for (const [k, v] of Object.entries(s)) if (f.elements[k]) f.elements[k].value = v;
  setSettingsDirty(false);
}

function setSettingsDirty(on) {
  settingsDirty = on;
  show($("#settings-dirty"), on);
  if (state) updateReady();
}

$("#settings").addEventListener("input", () => setSettingsDirty(true));
$("#settings").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {};
  for (const input of f.querySelectorAll("input[name], select[name]")) body[input.name] = input.value;
  banner($("#settings-error"), "");
  try {
    state = await api("/api/settings", body);
    fillSettings(state.settings);
    flash($("#settings-saved"));
    $("#sync-remote").textContent = state.settings.EXPECTED_REMOTE;
    updateReady();
  } catch (e) {
    banner($("#settings-error"), e.message);
  }
});

$("#check-remote").addEventListener("click", async () => {
  const out = $("#remote-result");
  const btn = $("#check-remote");
  btn.disabled = true;
  out.className = "result";
  out.textContent = "Checking…";
  show(out, true);
  try {
    const r = await api("/api/check-remote", { remote: $("#settings").elements.EXPECTED_REMOTE.value });
    out.className = "result " + (r.ok ? "ok" : "err");
    out.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
  } catch (e) {
    out.className = "result err";
    out.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------------------------------------ setup: projects
function setReposDirty(on) {
  reposDirty = on;
  show($("#repos-dirty"), on);
  if (state) updateReady();
}

function takeRepos(repos) {
  entries = repos.map((r) => r.entry);
  paused = new Set(repos.filter((r) => r.paused).map((r) => r.entry));
  statuses = new Map(repos.map((r) => [r.entry, r]));
  renderRepos();
}

function renderRepos() {
  const list = $("#repo-list");
  list.replaceChildren();
  for (const entry of entries) {
    const st = statuses.get(entry);
    const isPaused = paused.has(entry);
    const li = el("li", [st && !st.ok ? "bad" : "", isPaused ? "paused" : ""].join(" ").trim());
    li.append(el("span", "dot"));
    const what = el("div", "what");
    what.append(el("div", "entry", entry));
    const detail = st ? st.detail : "not saved yet";
    what.append(el("div", "detail", isPaused ? "paused, not read by runs · " + detail : detail));
    li.append(what);
    li.append(el("span", "tag", st ? st.kind : (/^[A-Za-z][A-Za-z0-9+.-]*:\/\/|^[^/\s@]+@[^/\s:]+:/.test(entry) ? "url" : "path")));
    const pause = el("button", "icon", isPaused ? "Resume" : "Pause");
    pause.type = "button";
    pause.setAttribute("aria-label", (isPaused ? "Resume " : "Pause ") + entry);
    pause.setAttribute("aria-pressed", String(isPaused));
    pause.title = isPaused
      ? "Read this project again (then Save projects). The next run adds all its dates, including those from while it was paused"
      : "Stop reading this project but keep it on the list (then Save projects). Dates already in your mirror stay";
    pause.addEventListener("click", () => {
      if (isPaused) paused.delete(entry); else paused.add(entry);
      setReposDirty(true);
      renderRepos();
      $("#repo-list").children[entries.indexOf(entry)].querySelector("button").focus();   // keep the keyboard place
    });
    li.append(pause);
    const rm = el("button", "icon", "Remove");
    rm.type = "button";
    rm.setAttribute("aria-label", "Remove " + entry);
    rm.title = "Take this project off the list (then Save projects). Nothing on disk is deleted";
    rm.addEventListener("click", () => {
      entries = entries.filter((x) => x !== entry);
      paused.delete(entry);
      setReposDirty(true);
      renderRepos();
    });
    li.append(rm);
    list.append(li);
  }
  show($("#repo-empty"), entries.length === 0);
}

function addEntries(list) {
  let added = 0;
  for (const raw of list) {
    const e = raw.trim();
    if (e && !entries.includes(e)) { entries.push(e); added++; }
  }
  if (added) { setReposDirty(true); renderRepos(); }
  return added;
}

$("#add-repo").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("#new-repo");
  addEntries(input.value.split(/\n/));
  input.value = "";
  input.focus();
});

$("#save-repos").addEventListener("click", async () => {
  banner($("#repos-error"), "");
  try {
    state = await api("/api/repos", { repos: entries, paused: entries.filter((e) => paused.has(e)) });
    takeRepos(state.repos);
    setReposDirty(false);
    show($("#repos-legacy"), state.repos_legacy);
    flash($("#repos-saved"));
    updateReady();
  } catch (e) {
    banner($("#repos-error"), e.message);
  }
});

$("#find").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const box = $("#find-result");
  box.replaceChildren(el("p", "hint", "Searching…"));
  try {
    const r = await api("/api/find-repos", { folder: $("#find-folder").value });
    box.replaceChildren();
    if (!r.found.length) { box.append(el("p", "hint", "No git repos found there.")); return; }
    if (!r.complete) box.append(el("p", "hint", "Stopped early (large folder): pick a narrower folder to see everything."));
    const ul = el("ul", "found");
    for (const path of r.found) {
      const li = el("li");
      const lab = el("label");
      const cb = el("input");
      cb.type = "checkbox";
      cb.value = path;
      cb.checked = !entries.includes(path);
      cb.disabled = entries.includes(path);
      lab.title = entries.includes(path) ? "Already in your project list" : "Tick to add this repo";
      lab.append(cb, document.createTextNode(path + (entries.includes(path) ? "  (already listed)" : "")));
      li.append(lab);
      ul.append(li);
    }
    const add = el("button", "secondary", "Add selected");
    add.type = "button";
    add.title = "Add the ticked repos to the list (then Save projects)";
    add.addEventListener("click", () => {
      const n = addEntries([...ul.querySelectorAll("input:checked:not(:disabled)")].map((c) => c.value));
      box.replaceChildren(el("p", "hint", `${n} added. Remember to save the projects.`));
    });
    box.append(ul, add);
  } catch (e) {
    box.replaceChildren(el("p", "result err", e.message));
  }
});

// ------------------------------------------------------------------------------------------------ sync
let job = null;          // {mode, running, lines[]}
let pollTimer = null;

function classify(line) {
  if (/^(warning:|WARNING)/i.test(line)) return "w";
  if (/^(error|FAILED|fatal|Traceback)|refusing to push|push failed/i.test(line)) return "e";
  if (/^SUMMARY |^(pushed|nothing to push)$|^mirror: /.test(line)) return "s";
  return "";
}

function renderLog() {
  const pre = $("#log");
  const only = $("#only-problems").checked;
  const lines = job ? job.lines : [];
  pre.replaceChildren();
  const shown = lines.filter((l) => !only || ["w", "e"].includes(classify(l)));
  if (!shown.length) {
    pre.append(el("span", "muted", job ? (job.running ? "Starting…" : "No output.") : "Nothing run yet."));
    return;
  }
  for (const line of shown) pre.append(el("span", classify(line), line + "\n"));
  pre.scrollTop = pre.scrollHeight;
}
$("#only-problems").addEventListener("change", renderLog);

function renderResult(r, mode, running) {
  const card = $("#results");
  if (!r || (!r.summary && !r.sources.length)) { show(card, false); return; }
  show(card, true);
  const problems = (job ? job.lines : []).filter((l) => ["w", "e"].includes(classify(l))).length;
  banner($("#run-warnings"), problems
    ? `${problems} warning${problems > 1 ? "s" : ""}: a source that could not be read counts as 0 below. See Output.` : "");
  const s = r.summary;
  $("#results-title").textContent = running ? "Running…" : mode === "preview" ? "Preview (nothing written)" : "Sync result";
  $("#t-repos").textContent = s ? s.repos : "–";
  $("#t-matching").textContent = s ? s.matching : "–";
  $("#t-mirrored").textContent = s ? s.mirrored : "–";
  $("#t-new").textContent = s ? s.new : "–";
  $("#t-new-cap").textContent = mode === "preview" ? "would be added" : "added";
  const body = $("#sources tbody");
  body.replaceChildren();
  for (const src of r.sources) {
    const tr = el("tr", src.new ? "has-new" : "");
    tr.append(el("td", "", src.name), el("td", "n", String(src.matching)), el("td", "n", String(src.mirrored)),
              el("td", "n", String(src.new)));
    body.append(tr);
  }
  renderCalendar(job && !running ? job.days : null, mode);
  $("#push-result").textContent = r.pushed === "pushed" ? "Pushed to your mirror repo. The graph can take a few minutes to update."
    : r.pushed === "nothing to push" ? "Your mirror repo was already up to date." : "";
}

function setRunning(on, mode) {
  $("#tab-sync").classList.toggle("busy", on);
  $("#run-preview").disabled = $("#run-sync").disabled = on;
  show($("#run-cancel"), on);
  const rs = $("#run-state");
  if (on) {
    rs.className = "pill run";
    rs.textContent = mode === "preview" ? "Previewing…" : "Syncing…";
    setStatus("run", rs.textContent);
  } else if (job) {
    const ok = job.exit_code === 0;
    rs.className = "pill " + (ok ? "ok" : "err");
    rs.textContent = ok ? (job.mode === "preview" ? "Preview done" : "Sync done") : `Failed (exit ${job.exit_code})`;
    if (state) updateReady();
  }
}

function resumeJob(view) {
  job = { mode: view.mode, running: view.running, exit_code: view.exit_code, lines: [] };
  poll(0);
}

async function poll(since) {
  clearTimeout(pollTimer);
  let v;
  try {
    v = await api("/api/job?since=" + since);
  } catch (e) {
    banner($("#fatal"), e.message);
    pollTimer = setTimeout(() => poll(since), 2000);
    return;
  }
  if (!v) return;
  job.lines = job.lines.concat(v.lines);
  job.running = v.running;
  job.exit_code = v.exit_code;
  job.mode = v.mode;
  job.days = v.days;
  renderLog();
  renderResult(v.result, v.mode, v.running);
  setRunning(v.running, v.mode);
  if (v.running) pollTimer = setTimeout(() => poll(v.since), 500);
}

async function run(mode) {
  try {
    const v = await api("/api/run", { mode });
    job = { mode, running: true, exit_code: null, lines: [] };
    renderLog();
    show($("#results"), false);
    setRunning(true, mode);
    poll(0);
    void v;
  } catch (e) {
    banner($("#fatal"), e.message);
  }
}

$("#run-preview").addEventListener("click", () => run("preview"));
$("#run-sync").addEventListener("click", () => {
  const d = $("#confirm");
  $("#confirm-text").textContent = "New dates are written to your mirror folder as empty commits and pushed to " +
    (state.settings.EXPECTED_REMOTE || "your mirror repo") + ". Only dates are written; no code, names or messages.";
  if (typeof d.showModal !== "function") { if (confirm($("#confirm-text").textContent)) run("sync"); return; }
  d.returnValue = "";
  d.showModal();
  d.addEventListener("close", () => { if (d.returnValue === "ok") run("sync"); }, { once: true });
});
$("#run-cancel").addEventListener("click", async () => {
  try { await api("/api/cancel", {}); } catch (e) { banner($("#fatal"), e.message); }
});

// ------------------------------------------------------------------------------------------------ calendar
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function renderCalendar(days, mode) {
  const wrap = $("#calendar-wrap");
  if (!days || !days.existing) { show(wrap, false); return; }
  show(wrap, true);
  const existing = days.existing || {}, added = days.new || {};
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  const start = new Date(today);
  start.setDate(start.getDate() - 364 - start.getDay());          // back to a Sunday, 53 columns
  const cells = [];
  let max = 0, newInWindow = 0;
  for (const d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const key = isoDay(d), before = existing[key] || 0, plus = added[key] || 0;
    cells.push({ key, before, plus });
    max = Math.max(max, before + plus);
    newInWindow += plus;
  }
  const level = (n) => n === 0 ? 0 : max <= 4 ? Math.min(4, n) : Math.min(4, Math.ceil((n / max) * 4));
  const cal = $("#calendar");
  cal.replaceChildren();
  for (const c of cells) {
    const i = el("i", "l" + level(c.before + c.plus) + (c.plus ? " new" : ""));
    const text = `${c.key}: ${c.before} already in the mirror` + (c.plus ? `, +${c.plus} ${mode === "preview" ? "would be added" : "added"}` : "");
    i.title = text;
    i.addEventListener("mouseenter", () => { $("#cal-day").textContent = text; });
    cal.append(i);
  }
  const totalNew = Object.values(added).reduce((a, b) => a + b, 0);
  const older = totalNew - newInWindow;
  cal.setAttribute("aria-label", `Contribution calendar: ${totalNew} new dates`);
  $("#cal-new-label").textContent = mode === "preview" ? "day with dates to add" : "day with dates just added";
  $("#cal-note").textContent = older > 0 ? `+${older} new dates older than 12 months (not shown)` : "";
}

// ------------------------------------------------------------------------------------------------ accounts
async function checkAccounts(recheck) {
  const st = $("#acc-state");
  banner($("#acc-error"), "");
  $("#acc-check").disabled = $("#acc-recheck").disabled = true;
  st.className = "pill run";
  st.textContent = recheck ? "Comparing logins…" : "Checking…";
  try {
    const r = await api("/api/accounts", { recheck });
    const body = $("#acc-table tbody");
    body.replaceChildren();
    for (const h of r.hosts) {
      const tr = el("tr");
      const kind = el("td", "", h.provider + (h.api && !h.reviews ? " · pull requests only" : ""));
      let who;
      if (!h.api) who = el("td", "muted", "commits only (no API for this host)");
      else if (!r.pr_reviews) who = el("td", "muted", "not needed (pull requests and reviews are off)");
      else if (h.login) {
        who = el("td");
        who.append(el("strong", "", h.login), document.createTextNode(" via "), el("code", "", h.source));
      } else who = el("td", "result err", h.error ? `no usable login: ${h.error}` : "no login found that can see these projects");
      tr.append(el("td", "", h.scope), kind, who, el("td", "n", String(h.projects)));
      body.append(tr);
    }
    show($("#acc-table"), r.hosts.length > 0);
    const notes = [];
    if (!r.hosts.length) notes.push("No project with a remote URL yet, so there is no host to log in to.");
    if (r.local_without_remote) notes.push(`${r.local_without_remote} local project(s) have no origin remote: their commits are mirrored, but no host is known for their pull requests.`);
    banner($("#acc-note"), notes.join(" "));
    const missing = r.pr_reviews && r.hosts.some((h) => h.api && !h.login);
    st.className = "pill " + (missing ? "warn" : "ok");
    st.textContent = missing ? "Some hosts have no login" : "OK";
  } catch (e) {
    banner($("#acc-error"), e.message);
    st.className = "pill err";
    st.textContent = "Failed";
  } finally {
    $("#acc-check").disabled = $("#acc-recheck").disabled = false;
  }
}
$("#acc-check").addEventListener("click", () => checkAccounts(false));
$("#acc-recheck").addEventListener("click", () => checkAccounts(true));

// ------------------------------------------------------------------------------------------------ check (doctor)
const CHECK_MARK = { ok: ["ok", "OK"], warn: ["warn", "Warning"], fail: ["err", "Problem"], skip: ["skip", "Not checked"] };

$("#doc-run").addEventListener("click", async () => {
  const st = $("#doc-state"), list = $("#doc-list"), btn = $("#doc-run");
  banner($("#doc-error"), "");
  btn.disabled = true;
  st.className = "pill run";
  st.textContent = "Checking…";
  try {
    const r = await api("/api/doctor", {});
    list.replaceChildren();
    for (const c of r.checks) {
      const [cls, label] = CHECK_MARK[c.status] || CHECK_MARK.skip;
      const li = el("li", "check-" + cls);
      li.append(el("span", "pill " + cls, label));
      const body = el("div", "what");
      const head = el("div");
      head.append(el("strong", "", c.name), document.createTextNode(" " + c.detail));
      body.append(head);
      if (c.fix && c.status !== "ok") body.append(el("div", "detail", "Fix: " + c.fix));
      li.append(body);
      list.append(li);
    }
    show(list, r.checks.length > 0);
    const fails = r.checks.filter((c) => c.status === "fail").length;
    const warns = r.checks.filter((c) => c.status === "warn").length;
    st.className = "pill " + (fails ? "err" : warns ? "warn" : "ok");
    st.textContent = fails ? `${fails} problem(s)` : warns ? `${warns} warning(s)` : "All good";
  } catch (e) {
    banner($("#doc-error"), e.message);
    st.className = "pill err";
    st.textContent = "Failed";
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------------------------------------ schedule
const SCHEDULERS = { linux: "systemd user timer", macos: "launchd", windows: "Task Scheduler" };

function fillSchedule(s) {
  const f = $("#sch-form");
  f.elements.login.checked = s.login;
  f.elements.daily.checked = s.daily;
  if (s.time) f.elements.time.value = s.time;
  f.elements.timezone.value = s.timezone || "";
  $("#sch-platform").textContent = SCHEDULERS[s.platform] || s.platform;
  show($("#sch-tz-field"), s.platform === "linux");
  banner($("#sch-unsupported"), s.supported ? "" : s.summary);
  f.querySelectorAll("input, button").forEach((x) => { x.disabled = !s.supported; });
  const pill = $("#sch-state");
  pill.className = "pill " + (s.login || s.daily ? "ok" : "warn");
  pill.textContent = s.summary.replace(/^automatic runs: /, "");
  if (s.ready !== undefined) show($("#sch-not-ready"), !s.ready && (s.login || s.daily));
}

async function loadSchedule() {
  try { fillSchedule(await api("/api/schedule")); } catch (e) { banner($("#sch-error"), e.message); }
}

async function applySchedule(body) {
  banner($("#sch-error"), "");
  const btns = $("#sch-form").querySelectorAll("button");
  btns.forEach((b) => { b.disabled = true; });
  try {
    const s = await api("/api/schedule", body);
    fillSchedule(s);
    $("#sch-steps").textContent = (s.steps || []).join("\n") || "Nothing needed changing.";
    show($("#sch-steps-box"), true);
    flash($("#sch-saved"));
    loadSchedule();
  } catch (e) {
    banner($("#sch-error"), e.message);
  } finally {
    btns.forEach((b) => { b.disabled = false; });
  }
}
$("#sch-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  applySchedule({ login: f.elements.login.checked, daily: f.elements.daily.checked, time: f.elements.time.value,
                  timezone: f.elements.timezone.value });
});
$("#sch-off").addEventListener("click", () => {
  const f = $("#sch-form");
  applySchedule({ login: false, daily: false, time: f.elements.time.value || "18:00", timezone: f.elements.timezone.value });
});

// ------------------------------------------------------------------------------------------------ logs
let logData = null;

function renderLogs() {
  if (!logData) return;
  $("#logs-dir").textContent = logData.dir;
  const chart = $("#runs-chart");
  chart.replaceChildren();
  const runs = logData.runs.filter((r) => r.mode === "apply");      // previews are not runs that changed anything
  const max = Math.max(1, ...runs.map((r) => r.new));
  for (const r of runs) {
    const b = el("button", r.new ? "" : "zero");
    b.type = "button";
    b.style.height = Math.max(3, Math.round((r.new / max) * 110)) + "px";
    const text = `${r.time} · ${r.mode === "apply" ? "sync" : "preview"}: ${r.new} ${r.mode === "apply" ? "created" : "would be created"}, ${r.matching} found in ${r.repos} sources, ${r.mirrored} already mirrored`;
    b.title = text;
    b.setAttribute("aria-label", text);
    const showIt = () => { $("#runs-detail").textContent = text; };
    b.addEventListener("mouseenter", showIt);
    b.addEventListener("focus", showIt);
    chart.append(b);
  }
  show(chart, runs.length > 0);
  show($("#runs-empty"), runs.length === 0);
  const last = runs[runs.length - 1];
  $("#runs-detail").textContent = last ? `Last sync ${last.time}: ${last.new} created. Point at a bar for details.` : "";
  const only = $("#logs-problems").checked;
  const body = $("#logs-table tbody");
  body.replaceChildren();
  const rows = logData.entries.filter((e) => !only || e.level === "WARNING" || e.level === "ERROR");
  for (const e of rows) {
    const tr = el("tr", e.level);
    tr.append(el("td", "", e.time), el("td", "", e.level), el("td", "", e.message));
    body.append(tr);
  }
  show($("#logs-table"), rows.length > 0);
  show($("#logs-empty"), rows.length === 0);
}
async function loadLogs() {
  try { logData = await api("/api/logs"); renderLogs(); } catch (e) { banner($("#fatal"), e.message); }
}
$("#logs-refresh").addEventListener("click", loadLogs);
$("#logs-problems").addEventListener("change", renderLogs);

// ------------------------------------------------------------------------------------------------ theme
// theme.js already applied the saved choice before the page was drawn; this only handles the switch.
function setTheme(choice) {
  const root = document.documentElement;
  if (choice === "light" || choice === "dark") root.setAttribute("data-theme", choice);
  else root.removeAttribute("data-theme");
  for (const b of document.querySelectorAll("[data-theme-choice]")) {
    b.setAttribute("aria-pressed", String(b.dataset.themeChoice === (choice || "system")));
  }
  try {
    if (choice === "light" || choice === "dark") localStorage.setItem("mirror-theme", choice);
    else localStorage.removeItem("mirror-theme");
  } catch (_) { /* not remembered, still applied */ }
}
for (const b of document.querySelectorAll("[data-theme-choice]")) {
  b.addEventListener("click", () => setTheme(b.dataset.themeChoice));
}
setTheme(document.documentElement.getAttribute("data-theme") || "system");

// ------------------------------------------------------------------------------------------------ tabs
const onOpen = { schedule: loadSchedule, logs: loadLogs };
function selectTab(name, focus) {
  for (const t of TABS) {
    const tab = $("#tab-" + t);
    tab.setAttribute("aria-selected", String(t === name));
    tab.tabIndex = t === name ? 0 : -1;
    show($("#view-" + t), t === name);
  }
  if (focus) $("#tab-" + name).focus();
  $("#tab-" + name).scrollIntoView({ block: "nearest", inline: "nearest" });   // narrow screens scroll the bar
  if (onOpen[name]) onOpen[name]();
  try { sessionStorage.setItem("mirror-tab", name); } catch (_) { /* optional */ }
}
for (const t of TABS) $("#tab-" + t).addEventListener("click", () => selectTab(t));
// arrow keys, Home and End move between tabs (the usual tab-list keyboard pattern)
$(".tabs").addEventListener("keydown", (ev) => {
  const i = TABS.findIndex((t) => $("#tab-" + t).getAttribute("aria-selected") === "true");
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[ev.key];
  if (next === undefined) return;
  ev.preventDefault();
  selectTab(TABS[(next + TABS.length) % TABS.length], true);
});
let savedTab = null;
try { savedTab = sessionStorage.getItem("mirror-tab"); } catch (_) { /* optional */ }
selectTab(START_TAB || (TABS.includes(savedTab) ? savedTab : "setup"));
requestAnimationFrame(() => requestAnimationFrame(() => $(".tabs").classList.add("ready")));

load();
