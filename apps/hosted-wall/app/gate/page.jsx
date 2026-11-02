const card = {
  width: "min(420px, 92vw)", margin: "12vh auto", padding: 24, border: "1px solid rgba(255,255,255,0.11)",
  borderRadius: 10, background: "rgba(15,15,16,0.94)",
};
const mono = { fontFamily: '"IBM Plex Mono", monospace', fontSize: 10, textTransform: "uppercase", color: "#99969e", margin: 0 };
const input = {
  width: "100%", boxSizing: "border-box", padding: "10px 12px", marginTop: 6,
  border: "1px solid rgba(255,255,255,0.2)", borderRadius: 6, background: "#050505", color: "#f4f2f5", font: "inherit",
};
const button = {
  marginTop: 14, width: "100%", padding: "10px 12px", border: "1px solid rgba(255,255,255,0.2)",
  borderRadius: 6, background: "#151516", color: "#f4f2f5", font: "inherit", fontWeight: 650, cursor: "pointer",
};

export default async function Gate({ searchParams }) {
  const params = await searchParams;
  const next = typeof params?.next === "string" && params.next.startsWith("/") ? params.next : "/citations";
  const error = params?.error === "1";
  return (
    <main style={card}>
      <p style={mono}>hosaka · citations</p>
      <h1 style={{ margin: "4px 0 12px", fontSize: 22 }}>Citations wall of shame</h1>
      <p style={{ color: "#99969e", fontSize: 13, margin: "0 0 16px" }}>
        Enter the party access code. This wall reads the shared Supabase workspace, so the party laptop does not have to stay on.
      </p>
      <form method="post" action="/api/gate">
        <input type="hidden" name="next" value={next} />
        <label style={mono}>
          access code
          <input style={input} name="code" type="password" autoComplete="one-time-code" maxLength={128} required autoFocus />
        </label>
        {error ? <p style={{ color: "#f3a7c8", fontSize: 13, margin: "10px 0 0" }}>That code was not accepted.</p> : null}
        <button style={button} type="submit">Enter</button>
      </form>
    </main>
  );
}
