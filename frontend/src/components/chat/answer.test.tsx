import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { newTurn, type ChatTurn } from "@/lib/chat/events";
import { renderWithProviders } from "@/test/render";

import { Answer } from "./answer";
import { UserMessage } from "./user-message";

const turn = (over: Partial<ChatTurn>): ChatTurn => ({ ...newTurn("t", "Q"), ...over });

function show(value: ChatTurn, locale: "en" | "ar" = "en") {
  return renderWithProviders(
    <Answer turn={value} canRetry onRetry={vi.fn()} onOpenInsight={vi.fn()} />,
    { locale },
  );
}

describe("UserMessage", () => {
  it.each(["en", "ar"] as const)("finds its own direction in %s", (locale) => {
    renderWithProviders(<UserMessage text="كم الإيراد؟" />, { locale });
    expect(screen.getByText("كم الإيراد؟")).toHaveAttribute("dir", "auto");
  });

  it("keeps the question's line breaks", () => {
    renderWithProviders(<UserMessage text={"one\ntwo"} />);
    expect(screen.getByTestId("user-message").querySelector("p")?.className).toContain("whitespace-pre-wrap");
  });
});

describe("Answer", () => {
  it("shows progress before the first word", () => {
    show(turn({ tools: [{ id: "a", name: "run_sql", arguments: {}, ok: null, summary: null, evidence: {} }] }));
    expect(screen.getByTestId("answer-progress")).toHaveTextContent("Working: Ran a SQL query");
  });

  it("copies the answer without its caveat lines", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    show(turn({ status: "done", answer: "Plain answer." }));
    await userEvent.click(await screen.findByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenCalledWith("Plain answer.");
  });

  it("offers no Copy until there is text", () => {
    show(turn({}));
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });

  it("marks a stopped answer and keeps its text", async () => {
    show(turn({ status: "stopped", answer: "Part of the answer" }));
    expect(await screen.findByText("Part of the answer")).toBeInTheDocument();
    expect(screen.getByTestId("answer-stopped")).toHaveTextContent("Stopped");
  });

  it("marks an answer cut off by a failure", async () => {
    show(turn({ status: "failed", answer: "Part", error: { code: "provider_timeout", retryAfterS: null } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("took too long");
    expect(screen.getByTestId("answer-stopped")).toHaveTextContent("cut off");
  });

  it("offers a retry for a stored question with no answer", async () => {
    const onRetry = vi.fn();
    renderWithProviders(
      <Answer turn={turn({ status: "unanswered" })} canRetry onRetry={onRetry} onOpenInsight={vi.fn()} />,
    );
    expect(screen.getByText("No answer was saved for this question.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("does not offer a retry on an older question", () => {
    renderWithProviders(
      <Answer turn={turn({ status: "unanswered" })} canRetry={false} onRetry={vi.fn()} onOpenInsight={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("names the answer in Arabic for screen readers", () => {
    show(turn({ status: "done", answer: "نص" }), "ar");
    expect(screen.getByRole("article", { name: "أجاب المحلل" })).toBeInTheDocument();
  });
});
