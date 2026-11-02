export default function Unconfigured() {
  return (
    <main style={{ width: "min(520px, 92vw)", padding: 24, border: "1px solid rgba(243,167,200,0.5)", borderRadius: 10 }}>
      <p style={{ fontFamily: '"IBM Plex Mono", monospace', fontSize: 10, textTransform: "uppercase", color: "#99969e", margin: 0 }}>gate ok · origin missing</p>
      <h1 style={{ margin: "4px 0 12px", fontSize: 22 }}>You are in, but the wall is not wired up yet.</h1>
      <p style={{ color: "#99969e", fontSize: 13, lineHeight: 1.5 }}>
        Set <code>CITATIONS_ORIGIN</code> on this Vercel project to the public HTTPS URL of the VirgoZone
        server (a tunnel or VPS exposing port 8069), redeploy, and this page becomes the citations wall.
      </p>
    </main>
  );
}
