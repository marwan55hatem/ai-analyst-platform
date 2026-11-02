import { describe, expect, it } from "vitest";

import { newTurn, reduceTurn, stopTurn, stripCaveats, type ChatEvent, type ChatTurn } from "./events";

const NOW = 1_000_000;

function play(events: ChatEvent[], start: ChatTurn = newTurn("t", "How much?")): ChatTurn {
  return events.reduce((turn, event) => reduceTurn(turn, event, NOW), start);
}

const CAVEAT = "Caveat: these numbers could not be traced to a tool result in this answer and may be wrong: 99.";

const final = (over: Partial<Extract<ChatEvent, { type: "final" }>> = {}): ChatEvent => ({
  type: "final",
  message_id: "m1",
  text: "The total.",
  untraced_numbers: [],
  numbers_checked: 2,
  confidence: { level: "medium", sample_size: 120 },
  scripted: true,
  ...over,
});

describe("reduceTurn", () => {
  it("appends tokens while streaming", () => {
    const turn = play([
      { type: "token", text: "Net " },
      { type: "token", text: "revenue" },
    ]);
    expect(turn.answer).toBe("Net revenue");
    expect(turn.status).toBe("streaming");
  });

  it("pairs a tool result with its call and keeps the order", () => {
    const turn = play([
      { type: "tool_call", id: "a", name: "run_sql", arguments: { sql: "SELECT 1" } },
      { type: "tool_call", id: "b", name: "make_chart", arguments: {} },
      { type: "tool_result", id: "a", name: "run_sql", ok: true, summary: "1 rows", evidence: { sql: "SELECT 1" } },
    ]);
    expect(turn.tools.map((t) => [t.id, t.ok])).toEqual([
      ["a", true],
      ["b", null],
    ]);
    expect(turn.tools[0]?.evidence.sql).toBe("SELECT 1");
  });

  it("starts the answer again after tools, as the stored turn does", () => {
    const turn = play([
      { type: "token", text: "Checking." },
      { type: "tool_call", id: "a", name: "run_sql", arguments: {} },
      { type: "token", text: "Answer" },
    ]);
    expect(turn.answer).toBe("Answer");
  });

  it("opens a wait on status and closes it on the next event", () => {
    const waiting = play([{ type: "status", code: "rate_limited", retry_in_s: 40 }]);
    expect(waiting.waitingUntil).toBe(NOW + 40_000);
    expect(reduceTurn(waiting, { type: "token", text: "a" }, NOW).waitingUntil).toBeNull();
  });

  it("handles a second wait in one question", () => {
    const turn = play([
      { type: "status", code: "rate_limited", retry_in_s: 10 },
      { type: "tool_call", id: "a", name: "run_sql", arguments: {} },
      { type: "status", code: "rate_limited", retry_in_s: 5 },
    ]);
    expect(turn.waitingUntil).toBe(NOW + 5_000);
  });

  it("takes the verified text from final and drops the caveat line", () => {
    const turn = play([
      { type: "token", text: "Draft" },
      { type: "caveat", kind: "untraced_numbers", message: CAVEAT, numbers: ["99"] },
      final({ text: `The total is 99.\n\n_${CAVEAT}_`, untraced_numbers: ["99"] }),
    ]);
    expect(turn.answer).toBe("The total is 99.");
    expect(turn.untraced).toEqual(["99"]);
    expect(turn.status).toBe("done");
    expect(turn.numbersChecked).toBe(2);
    expect(turn.confidence).toEqual({ level: "medium", sampleSize: 120 });
    expect(turn.scripted).toBe(true);
  });

  it("keeps partial text on an error", () => {
    const turn = play([
      { type: "token", text: "Part" },
      { type: "error", code: "provider_timeout", message: "Slow.", retry_after_s: 12 },
    ]);
    expect(turn.answer).toBe("Part");
    expect(turn.status).toBe("failed");
    expect(turn.error).toEqual({ code: "provider_timeout", retryAfterS: 12 });
    expect(turn.numbersChecked).toBeNull();
  });

  it("does not repeat the same caveat", () => {
    const caveat: ChatEvent = { type: "caveat", kind: "x", message: "Same." };
    expect(play([caveat, caveat]).caveats).toHaveLength(1);
  });
});

describe("stopTurn", () => {
  it("keeps the text, ends any wait and marks it stopped", () => {
    const turn = stopTurn(
      play([
        { type: "token", text: "Part" },
        { type: "status", code: "rate_limited", retry_in_s: 5 },
      ]),
    );
    expect(turn.answer).toBe("Part");
    expect(turn.status).toBe("stopped");
    expect(turn.waitingUntil).toBeNull();
  });

  it("leaves a finished turn alone", () => {
    const done = play([final()]);
    expect(stopTurn(done)).toBe(done);
  });
});

describe("stripCaveats", () => {
  it("leaves other italic text alone", () => {
    expect(stripCaveats("Keep _this_ line.", [{ kind: "x", message: "Other." }])).toBe("Keep _this_ line.");
  });

  it("removes every appended caveat", () => {
    const caveats = [
      { kind: "a", message: "One." },
      { kind: "b", message: "Two." },
    ];
    expect(stripCaveats("Text\n\n_One._\n\n_Two._", caveats)).toBe("Text");
  });
});
