import type { ChatEvent } from "./events";

const EVENT_TYPES = new Set(["token", "tool_call", "tool_result", "chart", "caveat", "status", "final", "error"]);

function parseFrame(frame: string): ChatEvent | null {
  let type = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!EVENT_TYPES.has(type) || data.length === 0) return null;
  try {
    return { type, ...(JSON.parse(data.join("\n")) as object) } as ChatEvent;
  } catch {
    return null; // one bad frame must not end the answer
  }
}

/**
 * Read a Server-Sent Events body as typed events. Comments (the `: keep-alive` heartbeat) and
 * unknown events are skipped. Bytes arrive in arbitrary pieces, so an event, or even one Arabic
 * letter, can span two reads.
 */
export async function* parseSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal?.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let end = buffer.indexOf("\n\n");
      while (end >= 0) {
        const event = parseFrame(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (event) yield event;
        end = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
