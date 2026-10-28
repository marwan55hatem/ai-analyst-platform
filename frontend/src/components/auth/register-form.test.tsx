import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { RegisterForm, localPasswordIssue, problemKey } from "./register-form";

vi.mock("@/lib/auth/auth-provider", () => ({ useAuth: () => ({ register: vi.fn() }) }));

describe("password rules", () => {
  it("mirrors the backend policy checks that need no word list", () => {
    expect(localPasswordIssue("short", "a@b.co", "Ann")).toBe("passwordShort");
    expect(localPasswordIssue("x".repeat(129), "a@b.co", "Ann")).toBe("passwordLong");
    expect(localPasswordIssue("mariam-2026-secure", "mariam@shop.co", "Lee")).toBe("passwordHasEmail");
    expect(localPasswordIssue("Mona Saleh 2026!", "x@y.co", "Mona Saleh")).toBe("passwordHasName");
    expect(localPasswordIssue("tide-lamp-ochre-91", "mariam@shop.co", "Mona Saleh")).toBeNull();
  });

  it("maps backend phrases to translated problems", () => {
    expect(problemKey("is too common")).toBe("common");
    expect(problemKey("must not contain your email name")).toBe("email");
    expect(problemKey("must be at least 10 characters")).toBe("short");
  });
});

describe("<RegisterForm>", () => {
  it("shows field errors linked to their inputs", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterForm />);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    const company = screen.getByLabelText("Company name");
    expect(company).toHaveAttribute("aria-invalid", "true");
    expect(company).toHaveAccessibleDescription("Enter your company name.");
  });

  it("shows Arabic validation messages", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterForm />, { locale: "ar" });
    await user.click(screen.getByRole("button", { name: "إنشاء الحساب" }));
    expect(screen.getByLabelText("اسم الشركة")).toHaveAccessibleDescription("أدخل اسم الشركة.");
  });
});
