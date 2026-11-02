"use client";

import { useEffect, useState } from "react";

const mono = { fontFamily: '"IBM Plex Mono", monospace', fontSize: 11, letterSpacing: "0.04em", textTransform: "uppercase", color: "#99969e" };

function when(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Wall() {
  const [wall, setWall] = useState(null);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let stop = false;
    let timer;
    const controller = new AbortController();
    async function pull() {
      try {
        const response = await fetch(`/api/wall?offset=${offset}&limit=50`, { cache: "no-store", signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Citations are temporarily unavailable.");
        if (!stop) {
          setWall(body);
          setError("");
        }
      } catch (err) {
        if (!stop && err?.name !== "AbortError") setError(err.message);
      } finally {
        if (!stop) timer = setTimeout(pull, document.hidden ? 60000 : 20000);
      }
    }
    pull();
    return () => {
      stop = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [offset]);

  const counts = wall?.counts;
  const normalizedQuery = query.trim().toLowerCase();
  const visible = (wall?.items ?? []).filter((row) => {
    if (filter === "overdue" && !row.overdue) return false;
    if (filter !== "all" && filter !== "overdue" && row.status !== filter) return false;
    if (!normalizedQuery) return true;
    const haystack = `${row.offender} ${row.violation} ${row.citation_number} ${row.description}`.toLowerCase();
    return haystack.includes(normalizedQuery);
  });

  return (
    <main style={{ width: "min(980px, 100%)", margin: "0 auto", padding: "28px 20px 64px" }}>
      <p style={mono}>hosaka · citations</p>
      <h1 style={{ margin: "6px 0 8px", fontSize: 28 }}>Wall of shame</h1>
      <p role="status" style={{ ...mono, textTransform: "none", letterSpacing: 0 }}>
        {error ? error : counts ? `${counts.open} open · ${counts.overdue} overdue · ${counts.resolved} resolved · ${counts.void} void` : "loading"}
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
        <input
          type="search"
          aria-label="Filter citations"
          placeholder="Search offender, violation, incident…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: "1 1 220px", minHeight: 40, padding: "8px 12px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.16)", background: "#0b0b0c", color: "#f4f2f5", font: "inherit" }}
        />
        {["all", "open", "overdue", "resolved"].map((mode) => (
          <button
            key={mode}
            type="button"
            aria-pressed={filter === mode}
            onClick={() => setFilter(mode)}
            style={{
              minHeight: 40, padding: "8px 14px", borderRadius: 8,
              border: filter === mode ? "1px solid #acebd1" : "1px solid rgba(255,255,255,0.14)",
              background: filter === mode ? "#182420" : "#121214",
              color: "#f4f2f5", font: "inherit", cursor: "pointer", textTransform: "capitalize",
            }}
          >
            {mode}
          </button>
        ))}
      </div>
      <ul style={{ listStyle: "none", padding: 0, margin: "22px 0 0", display: "grid", gap: 10 }}>
        {visible.map((row) => (
          <li key={row.id} style={{ border: "1px solid rgba(255,255,255,0.1)", borderRadius: 10, padding: "12px 14px", background: "rgba(15,15,16,0.94)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
              <strong>{row.display_code ? `${row.display_code} · ` : ""}{row.offender}</strong>
              <span style={{ ...mono, color: row.overdue ? "#f3a7c8" : "#99969e" }}>{row.overdue ? "overdue" : row.status}</span>
            </div>
            <div style={{ marginTop: 4, color: "#dcd6f7", fontWeight: 600 }}>{row.violation}</div>
            {row.description ? <p style={{ margin: "6px 0 0", color: "#e2deea", fontSize: 14 }}>Comment: {row.description}</p> : null}
            {(row.remedies?.length || row.resolution_notes) ? (
              <div style={{ marginTop: 8, fontSize: 13, color: "#b0f3db" }}>
                {row.remedies?.length ? (
                  <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 4 }}>
                    {row.remedies.map((rem, idx) => (
                      <li key={idx} style={{ opacity: rem.completed ? 0.85 : 1 }}>
                        Resolution: {rem.completed ? "[x]" : "[ ]"} {rem.description}
                        {rem.completion_notes ? ` (${rem.completion_notes})` : ""}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {row.resolution_notes ? (
                  <p style={{ margin: "4px 0 0" }}>Resolution note: {row.resolution_notes}</p>
                ) : null}
              </div>
            ) : null}
            <p style={{ ...mono, textTransform: "none", letterSpacing: 0, margin: "8px 0 0" }}>
              Issued {when(row.issued_at)}{row.issued_by ? ` by ${row.issued_by}` : ""}
              {row.due_at && row.status === "open" ? ` · Due ${when(row.due_at)}` : ""}
              {row.resolved_at ? ` · Resolved ${when(row.resolved_at)}${row.resolved_by ? ` by ${row.resolved_by}` : ""}` : ""}
            </p>
          </li>
        ))}
      </ul>
      <nav aria-label="Citation pages" style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))} style={{ minHeight: 44, padding: "8px 16px" }}>Previous</button>
        <span>{wall ? `${wall.total ? offset + 1 : 0}–${Math.min(offset + 50, wall.total)} of ${wall.total}` : "Loading…"}</span>
        <button type="button" disabled={!wall || offset + 50 >= wall.total} onClick={() => setOffset(offset + 50)} style={{ minHeight: 44, padding: "8px 16px" }}>Next</button>
      </nav>
    </main>
  );
}
