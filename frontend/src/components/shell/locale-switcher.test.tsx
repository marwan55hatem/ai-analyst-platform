import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { LocaleSwitcher } from "./locale-switcher";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

describe("<LocaleSwitcher>", () => {
  it("switches to Arabic: cookie, lang and dir", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LocaleSwitcher />);
    await user.click(screen.getByRole("button", { name: "Language: العربية" }));
    expect(document.cookie).toContain("NEXT_LOCALE=ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(refresh).toHaveBeenCalled();
  });

  it("offers English from the Arabic UI", () => {
    renderWithProviders(<LocaleSwitcher />, { locale: "ar" });
    expect(screen.getByRole("button", { name: "اللغة: English" })).toBeInTheDocument();
  });
});
