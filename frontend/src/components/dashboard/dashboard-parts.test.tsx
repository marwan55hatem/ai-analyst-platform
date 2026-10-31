import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LineBandChart } from "@/components/charts/line-band-chart";
import { CleaningPlan } from "@/components/datasets/cleaning-plan";
import type { AnomalyEvent, Fix } from "@/lib/api/types";
import { buildTrend, rankEvents } from "@/lib/charts/trend";
import { renderWithProviders } from "@/test/render";

import { EventList, TOP_EVENTS } from "./event-list";

function event(day: number, change: number, length = 1): AnomalyEvent {
  return {
    start: `2024-01-${String(day).padStart(2, "0")}`,
    end: `2024-01-${String(day).padStart(2, "0")}`,
    kind: change > 0 ? "spike" : "drop",
    actual: 0,
    expected: 0,
    change,
    peak_z: 7,
    length,
    insight_id: null,
  };
}

function fix(id: string, recommended: boolean): Fix {
  return {
    id,
    type: id,
    description: id,
    columns: [],
    rows_affected: 1,
    cells_affected: 1,
    risk: recommended ? "low" : "medium",
    recommended,
    strategies: null,
    default_strategy: null,
    strategy_risk: null,
    params: {},
  } as Fix;
}

const LABELS = {
  actual: "Net revenue",
  forecast: "Forecast",
  band80: "80% range",
  band95: "95% range",
  spike: "Unusual spike",
  drop: "Unusual drop",
  forecastStarts: "Forecast",
  partial: "partly in range",
  tickSpike: "Contains an unusual spike",
  tickDrop: "Contains an unusual drop",
};

describe("rankEvents", () => {
  it("orders by money away from normal times how long it lasted", () => {
    const ranked = rankEvents([event(1, 100), event(2, -300), event(3, 200, 2)]);
    expect(ranked.map((e) => e.start)).toEqual(["2024-01-03", "2024-01-02", "2024-01-01"]);
  });
});

describe("<EventList>", () => {
  it("shows the top ten of N, most severe first, and can show the rest", async () => {
    const events = Array.from({ length: 12 }, (_, i) => event(i + 1, (i + 1) * 100));
    renderWithProviders(<EventList events={events} onOpen={vi.fn()} />);
    const list = screen.getByTestId("event-list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(TOP_EVENTS);
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent(/12/);
    expect(screen.getByTestId("event-count")).toHaveTextContent("Top 10 of 12");
    await userEvent.click(screen.getByTestId("event-toggle"));
    expect(within(list).getAllByRole("listitem")).toHaveLength(12);
  });

  it("needs no count or toggle for ten or fewer", () => {
    renderWithProviders(<EventList events={[event(1, 100), event(2, -50)]} onOpen={vi.fn()} />);
    expect(screen.queryByTestId("event-count")).not.toBeInTheDocument();
    expect(screen.queryByTestId("event-toggle")).not.toBeInTheDocument();
  });

  it("opens the insight behind an event", async () => {
    const onOpen = vi.fn();
    renderWithProviders(<EventList events={[{ ...event(1, 100), insight_id: "abc" }]} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith("abc");
  });
});

describe("<LineBandChart> anomaly markers", () => {
  const points = buildTrend(
    [
      { period: "2024-01-01", value: 10 },
      { period: "2024-02-01", value: 20 },
    ],
    [],
    [event(9, 100)],
  );
  const props = {
    points,
    labels: LABELS,
    formatValue: String,
    formatTick: String,
    formatPeriod: (s: string) => s,
    ariaLabel: "Trend",
  };

  it("names a tick as a period that contains an unusual day", () => {
    renderWithProviders(<LineBandChart {...props} markerStyle="tick" />);
    expect(screen.getByText("Contains an unusual spike")).toBeInTheDocument();
    expect(screen.queryByText("Unusual spike")).not.toBeInTheDocument();
  });

  it("keeps point wording at day grain", () => {
    renderWithProviders(<LineBandChart {...props} />);
    expect(screen.getByText("Unusual spike")).toBeInTheDocument();
  });

  it("draws the 80% swatch over the 95% one, as the chart does", () => {
    const withForecast = buildTrend(
      [{ period: "2024-01-01", value: 10 }],
      [{ period: "2024-02-01", point: 12, lo80: 11, hi80: 13, lo95: 10, hi95: 14 }],
    );
    renderWithProviders(<LineBandChart {...props} points={withForecast} />);
    expect(screen.getByTestId("swatch-80").querySelectorAll("rect")).toHaveLength(3);
    expect(screen.getByTestId("swatch-95").querySelectorAll("rect")).toHaveLength(2);
  });
});

describe("<CleaningPlan> intro", () => {
  it("says fixes are selected only when some are", () => {
    renderWithProviders(<CleaningPlan datasetId="d1" version={1} fixes={[fix("trim", true), fix("dedupe", false)]} />);
    expect(screen.getByTestId("plan-intro")).toHaveTextContent("Recommended fixes are selected for you");
  });

  it("says nothing is selected when no fix is recommended", () => {
    renderWithProviders(<CleaningPlan datasetId="d1" version={1} fixes={[fix("dedupe", false)]} />);
    expect(screen.getByTestId("plan-intro")).toHaveTextContent("nothing is selected");
    expect(screen.getByTestId("plan-intro")).not.toHaveTextContent("selected for you");
  });
});
