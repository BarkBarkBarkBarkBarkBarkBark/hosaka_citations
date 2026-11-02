"use strict";
const $ = (id) => document.getElementById(id);
let offset = Math.max(0, Number(new URL(location.href).searchParams.get("offset")) || 0);
let total = 0;
let generation = 0;
let record = null;
let draft = false;
const page = new URL(location.href);
const base = new URL("./", location.href);

async function api(path, body) {
  const response = await fetch(new URL(path, base), body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Request failed. Inspect the citation before retrying a submission.");
  return result;
}
const action = (name, params = {}) => api("api/actions", { action: name, params });
function report(error) { $("message").textContent = error.message; }
function tab(name) {
  for (const id of ["list", "issue", "status", "detail"]) $(id).hidden = id !== name;
  for (const button of document.querySelectorAll("[data-tab]")) button.setAttribute("aria-pressed", String(button.dataset.tab === name));
}
function when(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function button(text, handler) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  element.addEventListener("click", async () => {
    element.disabled = true;
    try { await handler(); } catch (error) { report(error); }
    finally { element.disabled = false; }
  });
  return element;
}
async function load() {
  const current = ++generation;
  const params = new URLSearchParams({ q: $("query").value, status: $("status-filter").value, offset, limit: 50 });
  const result = await api("api/wall?" + params);
  if (current !== generation) return;
  total = result.total;
  $("rows").replaceChildren();
  for (const row of result.items) {
    const item = button("", () => detail(row.id));
    item.className = "citation";
    const name = document.createElement("strong");
    name.textContent = `${row.display_code || "#" + row.id.slice(0, 4)} · ${row.offender}`;
    const text = document.createElement("span");
    text.textContent = `${row.violation} — ${row.overdue ? "OVERDUE" : row.status}`;
    item.append(name, text);
    if (row.description) {
      const comment = document.createElement("p");
      comment.className = "citation-comment";
      comment.textContent = `Comment: ${row.description}`;
      item.append(comment);
    }
    const resParts = [];
    for (const r of row.remedies || []) {
      const note = r.completion_notes ? ` (${r.completion_notes})` : "";
      resParts.push(`${r.completed ? "[x]" : "[ ]"} ${r.description}${note}`);
    }
    if (row.resolution_notes) resParts.push(`Note: ${row.resolution_notes}`);
    if (resParts.length) {
      const resolution = document.createElement("p");
      resolution.className = "citation-resolution";
      resolution.textContent = `Resolution: ${resParts.join(" · ")}`;
      item.append(resolution);
    }
    const meta = document.createElement("small");
    const dates = [`Issued ${when(row.issued_at)}${row.issued_by ? " by " + row.issued_by : ""}`];
    if (row.due_at && row.status === "open") dates.push(`Due ${when(row.due_at)}`);
    if (row.resolved_at) dates.push(`Resolved ${when(row.resolved_at)}${row.resolved_by ? " by " + row.resolved_by : ""}`);
    meta.textContent = dates.join(" · ");
    item.append(meta);
    $("rows").append(item);
  }
  $("count").textContent = total ? `${offset + 1}-${Math.min(offset + 50, total)} of ${total}` : "No citations match.";
  $("previous").disabled = offset === 0;
  $("next").disabled = offset + 50 >= total;
  page.searchParams.set("q", $("query").value);
  page.searchParams.set("status", $("status-filter").value);
  page.searchParams.set("offset", offset);
  history.replaceState(null, "", page);
}
async function detail(id, push = true) {
  const result = await action("get", { citation: id });
  record = result.record;
  $("detail-title").textContent = `${record.display_code || ""} ${record.offender}`.trim();
  const detailParts = [`${record.violation} — ${record.status}`, `Issued ${when(record.issued_at)}${record.issued_by ? " by " + record.issued_by : ""}`];
  if (record.description) detailParts.push(`Comment: ${record.description}`);
  if (record.resolved_at) detailParts.push(`Resolved ${when(record.resolved_at)}${record.resolved_by ? " by " + record.resolved_by : ""}${record.resolution_notes ? " (" + record.resolution_notes + ")" : ""}`);
  $("detail-text").textContent = detailParts.join(" · ");
  $("receipt").textContent = result.receipt;
  $("remedies").replaceChildren();
  for (const remedy of record.remedies) {
    const item = document.createElement("li");
    const doneMeta = remedy.completed ? ` — completed ${when(remedy.completed_at)}${remedy.completed_by ? " by " + remedy.completed_by : ""}${remedy.completion_notes ? ": " + remedy.completion_notes : ""}` : "";
    item.textContent = `${remedy.completed ? "[x]" : "[ ]"} ${remedy.description}${doneMeta}`;
    if (!remedy.completed && record.status === "open") item.append(button("Mark performed", async () => {
      if (!confirm(`Confirm actually performed: ${remedy.description}?`)) return;
      await mutate("complete-remedy", { remedy_id: remedy.id });
    }));
    $("remedies").append(item);
  }
  $("detail-actions").replaceChildren();
  if (record.status === "open") {
    $("detail-actions").append(button("Add remedy", async () => {
      const description = prompt("Required remedy:");
      if (description?.trim()) await mutate("add-remedy", { description: description.trim(), required: true });
    }), button("Edit incident", async () => {
      const description = prompt("Updated incident description:", record.description || "");
      if (description !== null) await mutate("edit", { description: description.trim() });
    }), button("Resolve", async () => {
      if (record.remedies.some((item) => item.required && !item.completed)) throw new Error("Complete required remedies before resolving.");
      if (confirm("Resolve this citation?")) await mutate("resolve");
    }), button("Void", async () => {
      const reason = prompt("Reason for voiding this citation:");
      if (reason?.trim()) await mutate("void", { reason: reason.trim() });
    }));
  } else if (record.status === "resolved") {
    $("detail-actions").append(button("Reopen", async () => {
      const reason = prompt("Reason for reopening:");
      if (reason?.trim()) await mutate("reopen", { reason: reason.trim() });
    }));
  }
  const historyList = $("history-list");
  if (historyList) {
    historyList.replaceChildren();
    action("history", { citation: record.id }).then((hist) => {
      for (const evt of hist.rows || []) {
        const li = document.createElement("li");
        li.textContent = `${evt.occurred_at || ""} · ${evt.event_type}${evt.actor ? " by " + evt.actor : ""}${evt.notes ? ": " + evt.notes : ""}`;
        historyList.append(li);
      }
    }).catch(() => {});
  }
  page.searchParams.set("citation", record.id);
  history[push ? "pushState" : "replaceState"](null, "", page);
  tab("detail");
  $("detail-title").focus();
}
async function mutate(name, extra = {}) {
  await action(name, { citation: record.id, expected_revision: record.revision, ...extra });
  $("message").textContent = "Saved locally. Synchronization is separate.";
  await detail(record.id, false);
}
for (const item of document.querySelectorAll("[data-tab]")) item.addEventListener("click", () => {
  page.searchParams.delete("citation");
  history.pushState(null, "", page);
  tab(item.dataset.tab);
  if (item.dataset.tab === "status") syncStatus().catch(report);
});
$("filters").addEventListener("submit", (event) => {
  event.preventDefault(); offset = 0; load().catch(report);
});
$("next").addEventListener("click", () => { offset += 50; load().catch(report); });
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - 50); load().catch(report); });
$("detail-close").addEventListener("click", () => {
  page.searchParams.delete("citation"); history.pushState(null, "", page); tab("list"); load().catch(report);
});
$("issue-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = Object.fromEntries(new FormData(event.currentTarget));
  if (!params.remedy.trim()) delete params.remedy;
  if (!confirm(`Issue ${params.violation} to ${params.display_name}?`)) return;
  const submit = event.currentTarget.querySelector("button");
  submit.disabled = true;
  try {
    const result = await action("issue-for-attendee", params);
    $("message").textContent = "Saved locally. Synchronization is separate.";
    $("issue-form").reset();
    draft = false;
    await detail(result.record.id);
  } catch (error) { report(error); }
  finally { submit.disabled = false; }
});
$("issue-form").addEventListener("input", () => { draft = true; });
window.addEventListener("beforeunload", (event) => {
  if (draft) { event.preventDefault(); event.returnValue = ""; }
});
async function syncStatus() {
  const [syncRes, dashRes, confRes] = await Promise.all([
    action("sync-status"),
    action("dashboard").catch(() => ({ record: null })),
    action("sync-conflicts").catch(() => ({ rows: [] })),
  ]);
  $("sync-status").textContent = JSON.stringify(syncRes.record, null, 2);
  const stats = $("dashboard-stats");
  if (stats && dashRes.record) {
    stats.replaceChildren();
    for (const [label, key] of [["Open", "open"], ["Overdue", "overdue"], ["Resolved", "resolved"], ["Void", "void"], ["Pending sync", null]]) {
      const card = document.createElement("div");
      card.className = "stat-card";
      const val = key ? dashRes.record[key] : syncRes.record.pending_changes;
      card.textContent = `${label}: ${val ?? 0}`;
      stats.append(card);
    }
  }
  const conflicts = $("conflicts");
  if (conflicts) {
    conflicts.replaceChildren();
    for (const item of confRes.rows || []) {
      const row = document.createElement("div");
      row.className = "conflict-card";
      const text = document.createElement("p");
      text.textContent = `${item.label} — ${item.detail}`;
      row.append(text, button("Keep local", async () => {
        await action("sync-resolve", { conflict_id: item.conflict_id, choice: "local" });
        await syncStatus();
      }), button("Keep remote", async () => {
        await action("sync-resolve", { conflict_id: item.conflict_id, choice: "remote" });
        await syncStatus();
      }));
      conflicts.append(row);
    }
  }
}
$("sync-now").addEventListener("click", async () => {
  $("sync-now").disabled = true;
  try { await action("sync-now"); await syncStatus(); }
  catch (error) { report(error); }
  finally { $("sync-now").disabled = false; }
});
window.addEventListener("popstate", () => {
  page.search = new URL(location.href).search;
  $("query").value = page.searchParams.get("q") || "";
  $("status-filter").value = page.searchParams.get("status") || "all";
  offset = Math.max(0, Number(page.searchParams.get("offset")) || 0);
  const id = page.searchParams.get("citation");
  if (id) detail(id, false).catch(report); else { tab("list"); load().catch(report); }
});
$("query").value = page.searchParams.get("q") || "";
$("status-filter").value = page.searchParams.get("status") || "all";
Promise.all([load(), action("list-violations").then((result) => {
  for (const row of result.rows) {
    const option = document.createElement("option"); option.value = row.title; $("violations").append(option);
  }
})]).then(() => { if (page.searchParams.has("citation")) return detail(page.searchParams.get("citation"), false); }).catch(report);
