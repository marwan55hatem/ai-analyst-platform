import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { newTurn, type ChatTurn } from "@/lib/chat/events";
import { renderWithProviders } from "@/test/render";

import { TrustSignals } from "./trust-signals";

const turn = (over: Partial<ChatTurn>): ChatTurn => ({
  ...newTurn("t", "Q"),
  status: "done",
  answer: "It is 5.",
  ...over,
});

describe("TrustSignals", () => {
  it("counts the numbers traced to the data", () => {
    renderWithProviders(<TrustSignals turn={turn({ numbersChecked: 3 })} />);
    expect(screen.getByTestId("verifier-ok")).toHaveTextContent("3 numbers traced to your data");
  });

  it("says nothing about numbers when the answer has none", () => {
    renderWithProviders(<TrustSignals turn={turn({ numbersChecked: 0 })} />);
    expect(screen.queryByTestId("verifier-ok")).not.toBeInTheDocument();
    expect(screen.queryByTestId("verifier-unchecked")).not.toBeInTheDocument();
  });

  it("warns with the count and each untraced number, and shows no success badge", () => {
    renderWithProviders(<TrustSignals turn={turn({ numbersChecked: 3, untraced: ["9,876.21", "42%"] })} />);
    const warning = screen.getByTestId("verifier-warning");
    expect(warning).toHaveTextContent("2 numbers could not be traced to your data");
    expect(warning).toHaveTextContent("9,876.21");
    expect(warning).toHaveTextContent("42%");
    expect(screen.queryByTestId("verifier-ok")).not.toBeInTheDocument();
  });

  it.each(["stopped", "failed"] as const)("a %s answer is never shown as verified", (status) => {
    renderWithProviders(<TrustSignals turn={turn({ status, numbersChecked: null })} />);
    expect(screen.getByTestId("verifier-unchecked")).toHaveTextContent("Numbers not checked");
    expect(screen.queryByTestId("verifier-ok")).not.toBeInTheDocument();
  });

  it("says nothing while the answer is still streaming", () => {
    const { container } = renderWithProviders(<TrustSignals turn={turn({ status: "streaming" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing for an unchecked answer stored before the verifier count existed", () => {
    renderWithProviders(<TrustSignals turn={turn({ numbersChecked: null })} />);
    expect(screen.queryByTestId("verifier-unchecked")).not.toBeInTheDocument();
    expect(screen.queryByTestId("verifier-ok")).not.toBeInTheDocument();
  });

  it("shows confidence with its sample size", () => {
    renderWithProviders(<TrustSignals turn={turn({ confidence: { level: "low", sampleSize: 40 } })} />);
    expect(screen.getByText(/low confidence/i)).toBeInTheDocument();
    expect(screen.getByText(/40/)).toBeInTheDocument();
  });

  it("shows a rating with no sample size without inventing one", () => {
    renderWithProviders(<TrustSignals turn={turn({ confidence: { level: "medium", sampleSize: null } })} />);
    expect(screen.getByText(/medium confidence/i).textContent).not.toMatch(/\d/);
  });

  it("marks the scripted test model", () => {
    renderWithProviders(<TrustSignals turn={turn({ scripted: true })} />);
    expect(screen.getByTestId("test-model")).toHaveTextContent("Test model");
  });

  it("translates known caveats and hides the raw untraced one", () => {
    renderWithProviders(
      <TrustSignals
        turn={turn({
          caveats: [
            { kind: "answer_truncated", message: "english text" },
            { kind: "instruction_like_data", message: "english text 2" },
            { kind: "untraced_numbers", message: "raw", numbers: ["9"] },
          ],
          untraced: ["9"],
        })}
      />,
      { locale: "ar" },
    );
    expect(screen.getByText("بلغت هذه الإجابة حد الطول، لذا قد تكون ناقصة.")).toBeInTheDocument();
    expect(screen.getByText("يبدو جزء من بياناتك كأنه تعليمات. عومل كبيانات ولم يُنفذ.")).toBeInTheDocument();
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
  });

  it("shows an unknown caveat as the backend wrote it", () => {
    renderWithProviders(<TrustSignals turn={turn({ caveats: [{ kind: "new_kind", message: "New warning." }] })} />);
    expect(screen.getByText("New warning.")).toHaveAttribute("lang", "en");
  });
});
