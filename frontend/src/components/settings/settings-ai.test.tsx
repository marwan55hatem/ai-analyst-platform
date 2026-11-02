import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { AiModelCard, AiUsageCard } from "./ai-usage-card";
import { PrivacyCard } from "./privacy-card";

const USAGE = {
  tokens_used: 50_000,
  daily_budget: 200_000,
  resets_at: "2026-11-02T00:00:00Z",
  provider: "groq",
  model: "openai/gpt-oss-120b",
  model_strong: "openai/gpt-oss-120b",
};

function backend(usage: Partial<typeof USAGE> & Record<string, unknown>, allowPii = false) {
  server.use(
    http.get("*/api/v1/ai/usage", () => HttpResponse.json({ ...USAGE, ...usage })),
    http.get("*/api/v1/company/profile", () => HttpResponse.json({ allow_pii: allowPii })),
  );
}

describe("AiUsageCard", () => {
  it("states tokens, budget and reset time beside the bar", async () => {
    backend({});
    renderWithProviders(<AiUsageCard />);
    expect(await screen.findByText("50,000 of 200,000 tokens")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "AI tokens used today" })).toBeInTheDocument();
    expect(screen.getByText(/Resets at/)).toBeInTheDocument();
  });

  it("has its own wording near the limit", async () => {
    backend({ tokens_used: 180_000 });
    renderWithProviders(<AiUsageCard />);
    expect(await screen.findByText("Most of today's budget is used.")).toBeInTheDocument();
  });

  it("has its own wording at the limit", async () => {
    backend({ tokens_used: 200_000 });
    renderWithProviders(<AiUsageCard />);
    expect(await screen.findByText("Today's budget is used up. Answers resume after the reset.")).toBeInTheDocument();
  });

  it("keeps a bar in range when reservations pass the budget", async () => {
    backend({ tokens_used: 250_000 });
    renderWithProviders(<AiUsageCard />);
    const bar = await screen.findByRole("progressbar", { name: "AI tokens used today" });
    expect(bar).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("AiModelCard", () => {
  it("shows names only", async () => {
    backend({ provider: "groq", api_key: "must-not-show" });
    renderWithProviders(<AiModelCard />);
    expect((await screen.findAllByText("openai/gpt-oss-120b")).length).toBeGreaterThan(0);
    expect(screen.getByText("Groq")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("must-not-show");
  });

  it("says when the model is a test model", async () => {
    backend({ provider: "fake", model: null, model_strong: null } as never);
    renderWithProviders(<AiModelCard />);
    expect(await screen.findByText("Test model, nothing leaves this system")).toBeInTheDocument();
  });

  it("says when no provider is set up", async () => {
    backend({ provider: "none", model: null, model_strong: null } as never);
    renderWithProviders(<AiModelCard />);
    expect(await screen.findByText("No AI provider is set up")).toBeInTheDocument();
  });
});

describe("PrivacyCard", () => {
  it("states that personal data is off, and offers no switch", async () => {
    backend({}, false);
    renderWithProviders(<PrivacyCard />);
    expect(await screen.findByText(/Personal data is off/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("states that personal data is on", async () => {
    backend({}, true);
    renderWithProviders(<PrivacyCard />);
    expect(await screen.findByText(/Personal data is on/)).toBeInTheDocument();
  });

  it("lists what reaches the model, including the parts people assume stay private", async () => {
    backend({});
    renderWithProviders(<PrivacyCard />);
    expect(await screen.findByText(/up to 50 rows/)).toBeInTheDocument();
    expect(screen.getByText(/Customer IDs as they are/)).toBeInTheDocument();
    expect(screen.getByText(/isn't limited to totals/)).toBeInTheDocument();
  });

  it("says where the data goes with Groq", async () => {
    backend({ provider: "groq" });
    renderWithProviders(<PrivacyCard />);
    expect(await screen.findByText(/United States/)).toBeInTheDocument();
    expect(screen.getByText(/30 days/)).toBeInTheDocument();
  });

  it("does not mention the United States for the test model", async () => {
    backend({ provider: "fake" });
    renderWithProviders(<PrivacyCard />);
    expect(await screen.findByText(/none of this leaves our systems/)).toBeInTheDocument();
    expect(screen.queryByText(/United States/)).not.toBeInTheDocument();
  });

  it("speaks Arabic", async () => {
    backend({});
    renderWithProviders(<PrivacyCard />, { locale: "ar" });
    expect(await screen.findByText("ما الذي يصل إلى النموذج")).toBeInTheDocument();
  });
});
