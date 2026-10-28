import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Quality } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";

import { HealthPanel } from "./health-panel";
import { bandOf } from "./quality-bar";
import { QualityDial } from "./quality-dial";

const QUALITY: Quality = {
  version_id: "v2",
  version: 2,
  score: 88,
  breakdown: { completeness: 29, validity: 22, uniqueness: 15, consistency: 12, sufficiency: 10 },
  max: { completeness: 30, validity: 25, uniqueness: 15, consistency: 15, sufficiency: 15 },
  issues: [],
  sample_size: 300,
  confidence: "medium",
  method: "rules",
  warnings: [],
};

describe("<QualityDial>", () => {
  it("is a labelled meter with a spoken value and band", () => {
    renderWithProviders(<QualityDial score={57} />);
    const meter = screen.getByRole("meter", { name: "Quality score" });
    expect(meter).toHaveAttribute("aria-valuenow", "57");
    expect(meter).toHaveAttribute("aria-valuetext", "Quality score 57 out of 100. Needs cleaning first");
  });

  it("bands scores", () => {
    expect([bandOf(90), bandOf(70), bandOf(40)]).toEqual(["good", "fair", "poor"]);
  });
});

describe("<HealthPanel>", () => {
  it("shows the change from the version it was cleaned from", () => {
    renderWithProviders(<HealthPanel quality={QUALITY} previous={57} />);
    expect(screen.getByTestId("score-change")).toHaveTextContent("+31 points · Was 57 before cleaning");
    expect(screen.getByText("Completeness")).toBeInTheDocument();
    expect(screen.getByText("29 of 30")).toBeInTheDocument();
  });

  it("uses Latin digits in Arabic", () => {
    renderWithProviders(<HealthPanel quality={QUALITY} previous={null} />, { locale: "ar" });
    expect(screen.getByText("29 من 30")).toBeInTheDocument();
  });
});
