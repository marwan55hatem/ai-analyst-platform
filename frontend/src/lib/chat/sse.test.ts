import { describe, expect, it } from "vitest";

import { parseSse } from "./sse";

function stream(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
}

async function collect(body: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of parseSse(body)) events.push(event);
  return events;
}

const frame = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

describe("parseSse", () => {
  it("reads every event type in order", async () => {
    const body = stream(
      frame("token", { text: "Net " }),
      frame("tool_call", { id: "1", name: "run_sql", arguments: { sql: "SELECT 1" } }),
      frame("tool_result", { id: "1", name: "run_sql", ok: true, summary: "1 rows", evidence: {} }),
      frame("chart", { id: "1", spec: { type: "kpi", title: "T", columns: [], truncated: false } }),
      frame("caveat", { kind: "answer_truncated", message: "Cut off." }),
      frame("status", { code: "rate_limited", retry_in_s: 40 }),
      frame("final", { message_id: "m", text: "Net", untraced_numbers: [] }),
      frame("error", { code: "budget_exceeded", message: "Over." }),
    );
    expect((await collect(body)).map((e) => e.type)).toEqual([
      "token",
      "tool_call",
      "tool_result",
      "chart",
      "caveat",
      "status",
      "final",
      "error",
    ]);
  });

  it("skips keep-alive comments", async () => {
    const body = stream(": keep-alive\n\n", frame("token", { text: "a" }), ": keep-alive\n\n");
    expect(await collect(body)).toEqual([{ type: "token", text: "a" }]);
  });

  it("joins a frame split across chunks", async () => {
    const whole = frame("token", { text: "hello" });
    const body = stream(whole.slice(0, 9), whole.slice(9, 20), whole.slice(20));
    expect(await collect(body)).toEqual([{ type: "token", text: "hello" }]);
  });

  it("joins an Arabic character split across chunks", async () => {
    const bytes = new TextEncoder().encode(frame("token", { text: "إيراد" }));
    const cut = bytes.indexOf(0xd8) + 1; // inside the two bytes of the first letter
    expect(await collect(stream(bytes.slice(0, cut), bytes.slice(cut)))).toEqual([
      { type: "token", text: "إيراد" },
    ]);
  });

  it("accepts CRLF line endings", async () => {
    const body = stream(`event: token\r\ndata: {"text":"a"}\r\n\r\n`);
    expect(await collect(body)).toEqual([{ type: "token", text: "a" }]);
  });

  it("skips a malformed data line and keeps going", async () => {
    const body = stream("event: token\ndata: {oops\n\n", frame("token", { text: "b" }));
    expect(await collect(body)).toEqual([{ type: "token", text: "b" }]);
  });

  it("ignores event types it does not know", async () => {
    const body = stream(frame("future_thing", { a: 1 }), frame("token", { text: "b" }));
    expect(await collect(body)).toEqual([{ type: "token", text: "b" }]);
  });

  it("ends cleanly when the reader is cancelled by an abort", async () => {
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(frame("token", { text: "a" })));
      },
    });
    const seen = [];
    for await (const event of parseSse(body, controller.signal)) {
      seen.push(event);
      controller.abort();
    }
    expect(seen).toEqual([{ type: "token", text: "a" }]);
  });
});
