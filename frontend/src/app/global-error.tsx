"use client";

// Last-resort boundary: replaces the root layout, so no providers or translations exist
// here. Both languages are shown together.
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", background: "#f5f7f6", color: "#16201f" }}>
        <main style={{ maxWidth: "36rem" }}>
          <h1>Something went wrong</h1>
          <p>The page couldn&apos;t load. Try again in a moment.</p>
          <p lang="ar" dir="rtl">
            حدث خطأ ولم يُحمَّل التطبيق. حاول مرة أخرى بعد قليل.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{ padding: "0.5rem 1rem", background: "#0b5c63", color: "#fff", border: 0, borderRadius: 4 }}
          >
            Try again · حاول مرة أخرى
          </button>
        </main>
      </body>
    </html>
  );
}
