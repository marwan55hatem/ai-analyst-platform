import type { Message } from "@/lib/api/types";

import {
  newTurn,
  stripCaveats,
  wireConfidence,
  type Caveat,
  type ChartSpec,
  type ChatTurn,
  type ToolStep,
  type WireConfidence,
} from "./events";

function parseArguments(raw: unknown): unknown {
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function stepsOf(row: Message): ToolStep[] {
  return row.tool_calls.map((call) => ({
    id: String(call.id),
    name: String(call.name),
    arguments: parseArguments(call.arguments),
    ok: null,
    summary: null,
    evidence: {},
  }));
}

function addToolRow(turn: ChatTurn, row: Message): ChatTurn {
  const { chart, ...evidence } = row.evidence as Record<string, unknown> & { chart?: ChartSpec };
  return {
    ...turn,
    tools: turn.tools.map((tool) =>
      tool.id === row.tool_call_id
        ? {
            ...tool,
            ok: evidence.ok === true,
            summary: typeof evidence.summary === "string" ? evidence.summary : null,
            evidence,
          }
        : tool,
    ),
    charts: chart ? [...turn.charts, chart] : turn.charts,
    caveats: [...turn.caveats, ...(row.caveats as unknown as Caveat[])],
  };
}

function addAssistantRow(turn: ChatTurn, row: Message): ChatTurn {
  const evidence = row.evidence as {
    partial?: "stopped" | "failed";
    untraced_numbers?: string[];
    numbers_checked?: number;
    confidence?: WireConfidence | null;
    scripted?: boolean;
  };
  const caveats = [...turn.caveats, ...(row.caveats as unknown as Caveat[])];
  const next: ChatTurn = {
    ...turn,
    tools: [...turn.tools, ...stepsOf(row)],
    answer: row.content ? stripCaveats(row.content, caveats) : turn.answer,
    caveats,
  };
  if (evidence.partial) return { ...next, status: evidence.partial };
  if (!evidence.untraced_numbers) return next;
  return {
    ...next,
    status: "done",
    untraced: evidence.untraced_numbers,
    numbersChecked: evidence.numbers_checked ?? null,
    confidence: wireConfidence(evidence.confidence),
    scripted: Boolean(evidence.scripted),
  };
}

/**
 * Rebuild the turns of a conversation from its stored messages, so a reloaded answer shows the
 * same evidence and the same trust signals as the one that was streamed.
 */
export function turnsFromMessages(rows: readonly Message[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const row of rows) {
    if (row.role === "user") {
      turns.push({ ...newTurn(row.id, row.content), status: "unanswered" });
      continue;
    }
    const current = turns.pop();
    if (!current) continue;
    turns.push(row.role === "tool" ? addToolRow(current, row) : addAssistantRow(current, row));
  }
  return turns;
}

/**
 * What the page shows: stored turns, with this session's turns over them at their place in the
 * conversation. A conversation only grows, so a position stays true until the stored messages
 * catch up, which they do a little late after Stop.
 */
export function showTurns(
  stored: readonly ChatTurn[],
  kept: Readonly<Record<number, ChatTurn>>,
  live: ChatTurn | null,
  position: number,
): ChatTurn[] {
  const last = Math.max(stored.length, ...Object.keys(kept).map((key) => Number(key) + 1), live ? position + 1 : 0);
  const shown: ChatTurn[] = [];
  for (let index = 0; index < last; index += 1) {
    const turn = live && index === position ? live : (kept[index] ?? stored[index]);
    if (turn) shown.push(turn);
  }
  return shown;
}
