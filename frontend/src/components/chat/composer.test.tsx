import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { Composer, MAX_QUESTION } from "./composer";

function Harness({
  initial = "",
  streaming = false,
  onSend = vi.fn(),
  onStop = vi.fn(),
}: {
  initial?: string;
  streaming?: boolean;
  onSend?: (text: string) => void;
  onStop?: () => void;
}) {
  const [value, setValue] = useState(initial);
  return <Composer value={value} onChange={setValue} onSend={onSend} onStop={onStop} streaming={streaming} />;
}

const box = () => screen.getByRole("textbox", { name: "Your question" });

describe("Composer", () => {
  it("sends on Enter and trims the text", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Harness onSend={onSend} />);
    await userEvent.type(box(), "  How much?  {Enter}");
    expect(onSend).toHaveBeenCalledWith("How much?");
  });

  it("adds a line on Shift+Enter", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Harness onSend={onSend} />);
    await userEvent.type(box(), "one{Shift>}{Enter}{/Shift}two");
    expect(box()).toHaveValue("one\ntwo");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send while an input method is composing", () => {
    const onSend = vi.fn();
    renderWithProviders(<Harness initial="سؤال" onSend={onSend} />);
    fireEvent.keyDown(box(), { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send blank text", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Harness initial="   " onSend={onSend} />);
    await userEvent.type(box(), "{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("stays quiet about length until it matters", () => {
    renderWithProviders(<Harness initial="short" />);
    expect(screen.queryByTestId("composer-count")).not.toBeInTheDocument();
  });

  it("counts down near the limit", () => {
    renderWithProviders(<Harness initial={"x".repeat(MAX_QUESTION - 100)} />);
    expect(screen.getByTestId("composer-count")).toHaveTextContent("100 characters remaining");
  });

  it("blocks a question over the limit and says by how much", async () => {
    const onSend = vi.fn();
    renderWithProviders(<Harness initial={"x".repeat(MAX_QUESTION + 2)} onSend={onSend} />);
    expect(screen.getByTestId("composer-count")).toHaveTextContent("Shorten your question by 2 characters.");
    expect(box()).toHaveAttribute("aria-invalid", "true");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send while the conversation is still loading", () => {
    const onSend = vi.fn();
    renderWithProviders(<Composer value="Q" onChange={vi.fn()} onSend={onSend} onStop={vi.fn()} streaming={false} waiting />);
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("returns focus to the box after sending with the button", async () => {
    renderWithProviders(<Harness initial="Q" />);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(box()).toHaveFocus();
  });

  it("swaps Send for Stop while streaming, and Enter does nothing", async () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    renderWithProviders(<Harness initial="Next" streaming onSend={onSend} onStop={onStop} />);
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Stop answer" }));
    expect(onStop).toHaveBeenCalledOnce();
    expect(box()).toHaveFocus();
  });
});
