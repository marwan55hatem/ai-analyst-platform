import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { newTurn, type ChatTurn } from "@/lib/chat/events";
import { renderWithProviders } from "@/test/render";

import { Announcer } from "./announcer";
import { InsightChip } from "./insight-chip";
import { MessageList } from "./message-list";
import { StarterQuestions } from "./starter-questions";

const turn = (over: Partial<ChatTurn>): ChatTurn => ({ ...newTurn("t", "Q"), ...over });

describe("Announcer", () => {
  const region = () => screen.getByTestId("announcer");

  it("is one polite live region", () => {
    renderWithProviders(<Announcer turn={turn({})} />);
    expect(region()).toHaveAttribute("aria-live", "polite");
  });

  it("does not change with each token", () => {
    const { rerender } = renderWithProviders(<Announcer turn={turn({ answer: "A" })} />);
    const first = region().textContent;
    rerender(<Announcer turn={turn({ answer: "A longer answer" })} />);
    expect(region().textContent).toBe(first);
    expect(first).toBe("The analyst is answering.");
  });

  it("reads the whole answer when it is ready, without markdown marks", () => {
    renderWithProviders(<Announcer turn={turn({ status: "done", answer: "**Net** revenue | 5" })} />);
    expect(region()).toHaveTextContent("Answer ready. Net revenue 5");
  });

  it("says nothing when no answer is in progress", () => {
    renderWithProviders(<Announcer turn={null} />);
    expect(region()).toBeEmptyDOMElement();
  });
});

describe("InsightChip", () => {
  it("names the finding and can be removed", async () => {
    const onRemove = vi.fn();
    renderWithProviders(<InsightChip context={{ kind: "ready", title: "Revenue fell" }} onRemove={onRemove} />);
    expect(screen.getByTestId("insight-chip")).toHaveTextContent("Revenue fell");
    await userEvent.click(screen.getByRole("button", { name: "Remove this finding" }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("explains a finding that is gone", () => {
    renderWithProviders(<InsightChip context={{ kind: "missing" }} onRemove={vi.fn()} />);
    expect(screen.getByTestId("insight-note")).toHaveTextContent("That finding is gone");
  });

  it("explains a finding from an earlier version", () => {
    renderWithProviders(<InsightChip context={{ kind: "older" }} onRemove={vi.fn()} />);
    expect(screen.getByTestId("insight-note")).toHaveTextContent("earlier version");
  });

  it("shows nothing without a finding", () => {
    const { container } = renderWithProviders(<InsightChip context={{ kind: "none" }} onRemove={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("StarterQuestions", () => {
  it("asks the question it shows", async () => {
    const onAsk = vi.fn();
    renderWithProviders(<StarterQuestions keys={["revenue", "forecast"]} onAsk={onAsk} />);
    await userEvent.click(screen.getByRole("button", { name: "What is our total net revenue?" }));
    expect(onAsk).toHaveBeenCalledWith("What is our total net revenue?");
  });

  it("speaks the page's language", () => {
    renderWithProviders(<StarterQuestions keys={["forecast"]} onAsk={vi.fn()} />, { locale: "ar" });
    expect(screen.getByRole("button", { name: "توقع الإيرادات للأشهر القادمة" })).toBeInTheDocument();
  });
});

describe("MessageList", () => {
  function list(turns: ChatTurn[]) {
    return <MessageList turns={turns} onRetry={vi.fn()} onOpenInsight={vi.fn()} />;
  }

  function size(element: HTMLElement, scrollHeight: number) {
    Object.defineProperty(element, "scrollHeight", { value: scrollHeight, configurable: true });
    Object.defineProperty(element, "clientHeight", { value: 300, configurable: true });
  }

  it("follows the answer while the reader is at the bottom", () => {
    const { rerender } = renderWithProviders(list([turn({ answer: "a" })]));
    const box = screen.getByTestId("message-list");
    size(box, 1000);
    rerender(list([turn({ answer: "a longer answer" })]));
    expect(box.scrollTop).toBe(1000);
  });

  it("leaves the reader alone after they scroll up", () => {
    const { rerender } = renderWithProviders(list([turn({ answer: "a" })]));
    const box = screen.getByTestId("message-list");
    size(box, 1000);
    box.scrollTop = 100;
    fireEvent.scroll(box);
    rerender(list([turn({ answer: "a longer answer" })]));
    expect(box.scrollTop).toBe(100);
  });

  it("offers a retry only on the last question", () => {
    act(() => {
      renderWithProviders(list([turn({ id: "1", status: "unanswered" }), turn({ id: "2", status: "unanswered" })]));
    });
    expect(screen.getAllByRole("button", { name: "Try again" })).toHaveLength(1);
  });

  it("can be scrolled with the keyboard", () => {
    renderWithProviders(list([]));
    expect(screen.getByTestId("message-list")).toHaveAttribute("tabindex", "0");
  });
});
