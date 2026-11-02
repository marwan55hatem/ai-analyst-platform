import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, authFetch } from "@/lib/api/client";

import type { ChatTurn } from "./events";
import { useChatStream } from "./use-chat-stream";

vi.mock("@/lib/api/client", async (original) => ({
  ...(await original<typeof import("@/lib/api/client")>()),
  authFetch: vi.fn(),
}));

const frame = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
const FINAL = frame("final", { message_id: "m", text: "Done.", untraced_numbers: [], numbers_checked: 1 });

function body(...chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200 },
  );
}

/** A stream that stays open until the test ends it, like a slow model. */
function openStream(first: string) {
  const encoder = new TextEncoder();
  let handle: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      handle = controller;
      controller.enqueue(encoder.encode(first));
    },
  });
  return { response: new Response(stream, { status: 200 }), fail: () => handle.error(new TypeError("lost")) };
}

const mockFetch = vi.mocked(authFetch);
const settled = vi.fn();

function hook() {
  return renderHook(() => useChatStream({ onSettled: settled, fetchStored: fetchStored }));
}

const fetchStored = vi.fn<(id: string) => Promise<ChatTurn[]>>();

async function sent(index = 0): Promise<Record<string, unknown>> {
  const request = mockFetch.mock.calls[index]?.[0] as Request;
  return JSON.parse(await request.clone().text()) as Record<string, unknown>;
}

beforeEach(() => {
  mockFetch.mockReset();
  settled.mockReset();
  fetchStored.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useChatStream", () => {
  it("sends through authFetch and streams to the final answer", async () => {
    mockFetch.mockResolvedValue(body(frame("token", { text: "Do" }), FINAL));
    const { result } = hook();

    await act(() => result.current.ask("c1", "How much?"));

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(await sent()).toEqual({ conversation_id: "c1", message: "How much?", retry: false });
    expect(result.current.turn?.status).toBe("done");
    expect(result.current.turn?.answer).toBe("Done.");
    expect(settled).toHaveBeenCalledWith("c1");
  });

  it("keeps the text and marks the turn stopped on Stop", async () => {
    const open = openStream(frame("token", { text: "Part" }));
    mockFetch.mockImplementation(async (_request, init) => {
      init?.signal?.addEventListener("abort", open.fail);
      return open.response;
    });
    const { result } = hook();

    let finished: Promise<void> = Promise.resolve();
    act(() => {
      finished = result.current.ask("c1", "Q");
    });
    await waitFor(() => expect(result.current.turn?.answer).toBe("Part"));
    act(() => result.current.stop());
    await act(() => finished);

    expect(result.current.turn?.status).toBe("stopped");
    expect(result.current.turn?.answer).toBe("Part");
    expect(result.current.turn?.error).toBeNull();
  });

  it("reports a lost connection as network_error and keeps the text", async () => {
    const open = openStream(frame("token", { text: "Part" }));
    mockFetch.mockResolvedValue(open.response);
    const { result } = hook();

    let finished: Promise<void> = Promise.resolve();
    act(() => {
      finished = result.current.ask("c1", "Q");
    });
    await waitFor(() => expect(result.current.turn?.answer).toBe("Part"));
    open.fail();
    await act(() => finished);

    expect(result.current.turn?.status).toBe("failed");
    expect(result.current.turn?.error?.code).toBe("network_error");
    expect(result.current.turn?.answer).toBe("Part");
  });

  it("treats a stream that ends without a final event as lost", async () => {
    mockFetch.mockResolvedValue(body(frame("token", { text: "Part" })));
    const { result } = hook();

    await act(() => result.current.ask("c1", "Q"));

    expect(result.current.turn?.error?.code).toBe("network_error");
  });

  it("shows an HTTP error before the stream started", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ code: "rate_limited", message: "Slow down", details: { retry_after_seconds: 7 } }), {
        status: 429,
      }),
    );
    const { result } = hook();

    await act(() => result.current.ask("c1", "Q"));

    expect(result.current.turn?.error).toEqual({ code: "rate_limited", retryAfterS: 7 });
  });

  it("waits and tries again while the conversation is finishing, three times at most", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const busy = () =>
      new Response(JSON.stringify({ code: "conversation_busy", message: "busy", details: null }), { status: 429 });
    mockFetch.mockImplementation(async () => busy());
    const { result } = hook();

    let finished: Promise<void> = Promise.resolve();
    act(() => {
      finished = result.current.ask("c1", "Q");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
      await finished;
    });

    expect(mockFetch).toHaveBeenCalledTimes(4);
    expect(result.current.turn?.error?.code).toBe("conversation_busy");
  });

  it("gets through when the conversation frees up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "conversation_busy" }), { status: 429 }))
      .mockResolvedValueOnce(body(FINAL));
    const { result } = hook();

    let finished: Promise<void> = Promise.resolve();
    act(() => {
      finished = result.current.ask("c1", "Q");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
      await finished;
    });

    expect(result.current.turn?.status).toBe("done");
  });

  it("does not ask again when the answer turns out to be stored", async () => {
    fetchStored.mockResolvedValue([{ question: "Q", status: "done" } as ChatTurn]);
    const { result } = hook();

    await act(() => result.current.retry("c1", "Q"));

    expect(mockFetch).not.toHaveBeenCalled();
    expect(result.current.turn).toBeNull();
    expect(settled).toHaveBeenCalledWith("c1");
  });

  it("sends retry: true when the question has no answer yet", async () => {
    fetchStored.mockResolvedValue([{ question: "Q", status: "failed" } as ChatTurn]);
    mockFetch.mockResolvedValue(body(FINAL));
    const { result } = hook();

    await act(() => result.current.retry("c1", "Q"));

    expect((await sent()).retry).toBe(true);
    expect(result.current.turn?.status).toBe("done");
  });

  it("still retries when the stored messages cannot be read", async () => {
    fetchStored.mockRejectedValue(new ApiError(0, "network_error", ""));
    mockFetch.mockResolvedValue(body(FINAL));
    const { result } = hook();

    await act(() => result.current.retry("c1", "Q"));

    expect((await sent()).retry).toBe(true);
  });

  it("treats already_answered as finished", async () => {
    fetchStored.mockResolvedValue([]);
    mockFetch.mockResolvedValue(body(frame("error", { code: "already_answered", message: "Has one." })));
    const { result } = hook();

    await act(() => result.current.retry("c1", "Q"));

    expect(result.current.turn).toBeNull();
    expect(settled).toHaveBeenCalledWith("c1");
  });

  it("aborts when asked to clear, so a new conversation does not inherit the stream", async () => {
    const open = openStream(frame("token", { text: "Part" }));
    const abort = vi.fn();
    mockFetch.mockImplementation(async (_request, init) => {
      init?.signal?.addEventListener("abort", () => {
        abort();
        open.fail();
      });
      return open.response;
    });
    const { result } = hook();

    let finished: Promise<void> = Promise.resolve();
    act(() => {
      finished = result.current.ask("c1", "Q");
    });
    await waitFor(() => expect(result.current.turn?.answer).toBe("Part"));
    act(() => result.current.clear());
    await act(() => finished);

    expect(abort).toHaveBeenCalled();
    expect(result.current.turn).toBeNull();
  });
});
