export const metadata = {
  title: "Hosaka · Citations",
  description: "Citations wall of shame.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{
        margin: 0, minHeight: "100vh",
        background: "#050505", color: "#f4f2f5",
        fontFamily: '"IBM Plex Sans", "Noto Sans", sans-serif',
      }}>
        {children}
      </body>
    </html>
  );
}
