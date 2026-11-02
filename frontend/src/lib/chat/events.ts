import type { Confidence } from "@/lib/api/types";

/** A chart spec from the agent's chart tool (backend/app/agent/charts.py). */
export interface ChartSpecBase {
  title: string;
  columns: string[];
  truncated: boolean;
}
export type Cell = string | number | boolean | null;
export type ChartSpec = ChartSpecBase &
  (
    | { type: "kpi"; label: string; value: Cell }
    | { type: "line" | "bar" | "scatter"; x: string; y: string[]; data: Record<string, Cell>[] }
    | { type: "table"; rows: Cell[][] }
  );

export interface Caveat {
  kind: string;
  message: string;
  numbers?: string[];
  snippets?: string[];
}

export interface ToolStep {
  id: string;
  name: string;
  arguments: unknown;
  /** null while the tool runs, or when a stopped turn never finished it. */
  ok: boolean | null;
  summary: string | null;
  evidence: Record<string, unknown>;
}

export interface WireConfidence {
  level: Confidence;
  sample_size: number | null;
}

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "tool_call"; id: string; name: string; arguments: unknown }
  | { type: "tool_result"; id: string; name: string; ok: boolean; summary: string; evidence: Record<string, unknown> }
  | { type: "chart"; id: string; spec: ChartSpec }
  | ({ type: "caveat" } & Caveat)
  | { type: "status"; code: string; retry_in_s: number }
  | {
      type: "final";
      message_id: string | null;
      text: string;
      untraced_numbers: string[];
      numbers_checked?: number;
      confidence?: WireConfidence | null;
      scripted?: boolean;
    }
  | { type: "error"; code: string; message: string; retry_after_s?: number };

export interface ChatErrorInfo {
  code: string;
  retryAfterS: number | null;
}

export type TurnStatus = "streaming" | "done" | "stopped" | "failed" | "unanswered";

/** One question and its answer, built from the live stream or from stored messages. */
export interface ChatTurn {
  id: string;
  question: string;
  answer: string;
  status: TurnStatus;
  tools: ToolStep[];
  charts: ChartSpec[];
  caveats: Caveat[];
  untraced: string[];
  /** Numbers the verifier compared with the data; null when the answer was never checked. */
  numbersChecked: number | null;
  confidence: { level: Confidence; sampleSize: number | null } | null;
  waitingUntil: number | null;
  error: ChatErrorInfo | null;
  scripted: boolean;
  /** The next token starts a new answer: text before a tool call was only a preamble. */
  restartAnswer: boolean;
}

export function newTurn(id: string, question: string): ChatTurn {
  return {
    id,
    question,
    answer: "",
    status: "streaming",
    tools: [],
    charts: [],
    caveats: [],
    untraced: [],
    numbersChecked: null,
    confidence: null,
    waitingUntil: null,
    error: null,
    scripted: false,
    restartAnswer: false,
  };
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Remove the caveat lines the backend appends to an answer; the UI shows them as warnings. */
export function stripCaveats(text: string, caveats: readonly Caveat[]): string {
  return caveats.reduce(
    (out, caveat) => out.replace(new RegExp(`\\n\\n_${escapeRegExp(caveat.message)}_`), ""),
    text,
  );
}

export function wireConfidence(value: WireConfidence | null | undefined): ChatTurn["confidence"] {
  return value ? { level: value.level, sampleSize: value.sample_size ?? null } : null;
}

/** Apply one stream event. Any event ends a rate-limit wait: there is no "wait over" event. */
export function reduceTurn(turn: ChatTurn, event: ChatEvent, now: number): ChatTurn {
  const next: ChatTurn = { ...turn, waitingUntil: null };
  switch (event.type) {
    case "token":
      return {
        ...next,
        answer: turn.restartAnswer ? event.text : turn.answer + event.text,
        restartAnswer: false,
      };
    case "tool_call":
      return {
        ...next,
        restartAnswer: true,
        tools: [
          ...turn.tools,
          { id: event.id, name: event.name, arguments: event.arguments, ok: null, summary: null, evidence: {} },
        ],
      };
    case "tool_result":
      return {
        ...next,
        tools: turn.tools.map((tool) =>
          tool.id === event.id
            ? { ...tool, ok: event.ok, summary: event.summary, evidence: event.evidence }
            : tool,
        ),
      };
    case "chart":
      return { ...next, charts: [...turn.charts, event.spec] };
    case "caveat": {
      const { kind, message, numbers, snippets } = event;
      if (turn.caveats.some((c) => c.kind === kind && c.message === message)) return next;
      return { ...next, caveats: [...turn.caveats, { kind, message, numbers, snippets }] };
    }
    case "status":
      return { ...next, waitingUntil: now + event.retry_in_s * 1000 };
    case "final":
      return {
        ...next,
        status: "done",
        answer: stripCaveats(event.text, turn.caveats),
        untraced: event.untraced_numbers,
        numbersChecked: event.numbers_checked ?? null,
        confidence: wireConfidence(event.confidence),
        scripted: Boolean(event.scripted),
      };
    case "error":
      return {
        ...next,
        status: "failed",
        error: { code: event.code, retryAfterS: event.retry_after_s ?? null },
      };
  }
}

/** The user pressed Stop: what has arrived stays, marked as stopped. */
export function stopTurn(turn: ChatTurn): ChatTurn {
  return turn.status === "streaming" ? { ...turn, status: "stopped", waitingUntil: null } : turn;
}
