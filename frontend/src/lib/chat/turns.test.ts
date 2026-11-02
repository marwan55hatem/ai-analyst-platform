import { describe, expect, it } from "vitest";

import type { Message } from "@/lib/api/types";

import { newTurn, reduceTurn, stopTurn, type ChartSpec, type ChatEvent } from "./events";
import { showTurns, turnsFromMessages } from "./turns";

let seq = 0;

function row(over: Partial<Message>): Message {
  seq += 1;
  return {
    id: `id-${seq}`,
    seq,
    role: "user",
    content: "",
    tool_calls: [],
    tool_call_id: null,
    tool_name: null,
    evidence: {},
    caveats: [],
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    tokens_cached: 0,
    tokens_reasoning: 0,
    cost_usd_approx: 0,
    latency_ms: 0,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const user = (content: string) => row({ role: "user", content });
const calling = (id: string, name: string, args: string) =>
  row({ role: "assistant", tool_calls: [{ id, name, arguments: args }] });
const tool = (id: string, name: string, evidence: Record<string, unknown>) =>
  row({ role: "tool", tool_call_id: id, tool_name: name, content: "<<<wrapped>>>", evidence });
const answer = (content: string, evidence: Record<string, unknown>, caveats: Message["caveats"] = []) =>
  row({ role: "assistant", content, evidence, caveats });

const TRUST = { untraced_numbers: [], numbers_checked: 3, confidence: null, scripted: true, tool_calls: 1 };

describe("turnsFromMessages", () => {
  it("rebuilds a finished turn the same way the stream does", () => {
    const chart: ChartSpec = { type: "kpi", title: "T", columns: ["a"], truncated: false, label: "a", value: 1 };
    const stored = turnsFromMessages([
      user("How much?"),
      calling("c1", "make_chart", '{"sql":"SELECT 1"}'),
      tool("c1", "make_chart", { ok: true, summary: "kpi chart", sql: "SELECT 1", chart }),
      answer("The total.", { ...TRUST, confidence: { level: "low", sample_size: 40 } }),
    ])[0]!;

    const events: ChatEvent[] = [
      { type: "tool_call", id: "c1", name: "make_chart", arguments: { sql: "SELECT 1" } },
      {
        type: "tool_result",
        id: "c1",
        name: "make_chart",
        ok: true,
        summary: "kpi chart",
        evidence: { sql: "SELECT 1" },
      },
      { type: "chart", id: "c1", spec: chart },
      {
        type: "final",
        message_id: "m",
        text: "The total.",
        untraced_numbers: [],
        numbers_checked: 3,
        confidence: { level: "low", sample_size: 40 },
        scripted: true,
      },
    ];
    const live = events.reduce((turn, event) => reduceTurn(turn, event, 0), newTurn("x", "How much?"));

    expect(stored.status).toBe(live.status);
    expect(stored.answer).toBe(live.answer);
    expect(stored.numbersChecked).toBe(live.numbersChecked);
    expect(stored.confidence).toEqual(live.confidence);
    expect(stored.charts).toEqual(live.charts);
    expect(stored.scripted).toBe(live.scripted);
    expect(stored.tools[0]?.name).toBe("make_chart");
    expect(stored.tools[0]?.arguments).toEqual({ sql: "SELECT 1" });
    expect(stored.tools[0]?.ok).toBe(true);
    expect(stored.tools[0]?.summary).toBe("kpi chart");
  });

  it("marks a stopped answer and never reports its numbers as checked", () => {
    const stored = turnsFromMessages([user("Q"), answer("Part of it", { partial: "stopped", tool_calls: 0 })])[0]!;
    const live = stopTurn(reduceTurn(newTurn("x", "Q"), { type: "token", text: "Part of it" }, 0));
    expect(stored.status).toBe(live.status);
    expect(stored.answer).toBe(live.answer);
    expect(stored.numbersChecked).toBeNull();
  });

  it("marks a failed answer that kept its text", () => {
    const stored = turnsFromMessages([user("Q"), answer("Part", { partial: "failed", tool_calls: 0 })])[0]!;
    expect(stored.status).toBe("failed");
    expect(stored.answer).toBe("Part");
    expect(stored.numbersChecked).toBeNull();
  });

  it("calls a question with no answer unanswered", () => {
    expect(turnsFromMessages([user("Q")])[0]?.status).toBe("unanswered");
  });

  it("keeps the text shown before a stop in a tool round", () => {
    const stored = turnsFromMessages([
      user("Q"),
      row({ role: "assistant", content: "Checking.", tool_calls: [{ id: "c", name: "run_sql", arguments: "{}" }] }),
      answer("", { partial: "stopped", tool_calls: 1 }),
    ])[0]!;
    expect(stored.status).toBe("stopped");
    expect(stored.answer).toBe("Checking.");
    expect(stored.tools[0]?.ok).toBeNull();
  });

  it("treats messages stored before the trust fields as not counted", () => {
    const stored = turnsFromMessages([user("Q"), answer("Old", { untraced_numbers: [], tool_calls: 0 })])[0]!;
    expect(stored.status).toBe("done");
    expect(stored.numbersChecked).toBeNull();
    expect(stored.confidence).toBeNull();
  });

  it("shows untraced numbers and strips the stored caveat line", () => {
    const message = "Caveat: wrong: 99.";
    const stored = turnsFromMessages([
      user("Q"),
      answer(
        `It is 99.\n\n_${message}_`,
        { ...TRUST, untraced_numbers: ["99"] },
        [{ kind: "untraced_numbers", message, numbers: ["99"] }],
      ),
    ])[0]!;
    expect(stored.answer).toBe("It is 99.");
    expect(stored.untraced).toEqual(["99"]);
    expect(stored.caveats).toHaveLength(1);
  });

  it("collects caveats from tool rows", () => {
    const stored = turnsFromMessages([
      user("Q"),
      calling("c", "run_sql", "{}"),
      row({
        role: "tool",
        tool_call_id: "c",
        tool_name: "run_sql",
        evidence: { ok: true },
        caveats: [{ kind: "instruction_like_data", message: "Looks like instructions." }],
      }),
      answer("Done.", TRUST),
    ])[0]!;
    expect(stored.caveats.map((c) => c.kind)).toEqual(["instruction_like_data"]);
  });

  it("splits a conversation into one turn per question", () => {
    const turns = turnsFromMessages([user("One"), answer("A", TRUST), user("Two"), answer("B", TRUST)]);
    expect(turns.map((t) => [t.question, t.answer])).toEqual([
      ["One", "A"],
      ["Two", "B"],
    ]);
  });

  it("keeps broken tool arguments as text", () => {
    const stored = turnsFromMessages([user("Q"), calling("c", "run_sql", "{oops")])[0]!;
    expect(stored.tools[0]?.arguments).toBe("{oops");
  });
});

describe("showTurns", () => {
  const stored = [newTurn("a", "One"), newTurn("b", "Two")];
  const live = newTurn("live", "Three");

  it("shows the stored turns alone when nothing is live", () => {
    expect(showTurns(stored, {}, null, 0)).toEqual(stored);
  });

  it("adds the live turn at its position", () => {
    expect(showTurns(stored, {}, live, 2).map((t) => t.id)).toEqual(["a", "b", "live"]);
  });

  it("puts a live turn over the stored copy of the same question", () => {
    expect(showTurns(stored, {}, live, 1).map((t) => t.id)).toEqual(["a", "live"]);
  });

  it("keeps earlier finished turns until the stored ones catch up", () => {
    const earlier = newTurn("kept", "Two");
    expect(showTurns(stored.slice(0, 1), { 1: earlier }, live, 2).map((t) => t.id)).toEqual(["a", "kept", "live"]);
  });
});
