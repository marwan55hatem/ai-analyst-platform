/** Container healthcheck: the Next.js server is up (does not call the API). */
export function GET() {
  return Response.json({ status: "ok" });
}

export const dynamic = "force-dynamic";
