"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, authFetch, toApiError } from "@/lib/api/client";
import { retryAfterSeconds } from "@/lib/errors";

import { newTurn, reduceTurn, stopTurn, type ChatTurn } from "./events";
import { parseSse } from "./sse";

const STREAM_URL = "/api/v1/chat/stream";
const FLUSH_MS = 40; // tokens can arrive faster than the page can usefully draw them
const BUSY_RETRIES = 3;
const BUSY_WAIT_MS = 2_000;

interface Options {
  /** Called once a turn has ended and the server may have new messages. */
  onSettled: (conversationId: string) => void;
  /** Read a conversation's stored turns, to see whether a retry is still needed. */
  fetchStored: (conversationId: string) => Promise<ChatTurn[]>;
}

async function openStream(body: object, signal: AbortSignal): Promise<ReadableStream<Uint8Array>> {
  const request = new Request(`${window.location.origin}${STREAM_URL}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  const response = await authFetch(request, { signal });
  if (!response.ok) {
    throw toApiError(response.status, await response.json().catch(() => null));
  }
  if (!response.body) throw new ApiError(0, "network_error", "");
  return response.body;
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** Open the stream, waiting quietly while the conversation finishes its previous turn. */
async function openWhenFree(body: object, signal: AbortSignal): Promise<ReadableStream<Uint8Array>> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await openStream(body, signal);
    } catch (error) {
      const busy = error instanceof ApiError && error.code === "conversation_busy";
      if (!busy || attempt === BUSY_RETRIES || signal.aborted) throw error;
      await pause(BUSY_WAIT_MS, signal);
    }
  }
}

/**
 * The answer being streamed. Stop aborts the request and keeps what has arrived; a lost connection
 * keeps it too, with an error that can be retried without storing the question twice.
 */
export function useChatStream({ onSettled, fetchStored }: Options) {
  const [turn, setTurn] = useState<ChatTurn | null>(null);
  const live = useRef<ChatTurn | null>(null);
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setTurn(live.current);
  }, []);

  const flushSoon = useCallback(() => {
    timer.current ??= setTimeout(flush, FLUSH_MS);
  }, [flush]);

  const run = useCallback(
    async (conversationId: string, question: string, retry: boolean) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      live.current = newTurn(crypto.randomUUID(), question);
      flush();
      let ended = false;
      let answered = false;
      try {
        const body = await openWhenFree({ conversation_id: conversationId, message: question, retry }, abort.signal);
        for await (const event of parseSse(body, abort.signal)) {
          if (controller.current !== abort) break;
          if (event.type === "token" && !live.current?.answer) performance.mark("chat-first-token");
          if (event.type === "error" && event.code === "already_answered") {
            answered = true;
            break;
          }
          ended = event.type === "final" || event.type === "error";
          if (live.current) live.current = reduceTurn(live.current, event, Date.now());
          if (event.type === "token") flushSoon();
          else flush();
        }
      } catch (error) {
        if (!abort.signal.aborted && live.current) {
          const failed = error instanceof ApiError ? error : new ApiError(0, "network_error", "");
          live.current = reduceTurn(
            live.current,
            { type: "error", code: failed.code, message: "", retry_after_s: retryAfterSeconds(failed) ?? undefined },
            Date.now(),
          );
          ended = true;
        }
      }
      if (controller.current !== abort) return; // cleared or replaced: this turn is no longer shown
      controller.current = null;
      if (answered) live.current = null;
      else if (!ended && !abort.signal.aborted && live.current) {
        live.current = reduceTurn(live.current, { type: "error", code: "network_error", message: "" }, Date.now());
      }
      flush();
      onSettled(conversationId);
    },
    [flush, flushSoon, onSettled],
  );

  const ask = useCallback(
    (conversationId: string, question: string) => run(conversationId, question, false),
    [run],
  );

  /** Ask again without a second copy of the question; a stored answer is shown as it is. */
  const retry = useCallback(
    async (conversationId: string, question: string) => {
      const stored = await fetchStored(conversationId).catch(() => []);
      const last = stored.at(-1);
      if (last?.question === question && last.status === "done") {
        live.current = null;
        flush();
        onSettled(conversationId);
        return;
      }
      await run(conversationId, question, true);
    },
    [fetchStored, flush, onSettled, run],
  );

  const stop = useCallback(() => {
    if (live.current) live.current = stopTurn(live.current);
    flush();
    controller.current?.abort();
  }, [flush]);

  /** Forget the turn, for example when another conversation opens. */
  const clear = useCallback(() => {
    const running = controller.current;
    controller.current = null;
    running?.abort();
    live.current = null;
    flush();
  }, [flush]);

  useEffect(() => () => controller.current?.abort(), []);

  return { turn, ask, retry, stop, clear };
}
