import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { checkFile, nameFromFile } from "@/lib/api/upload";
import { MAX_UPLOAD_BYTES } from "@/lib/config";
import { renderWithProviders } from "@/test/render";

import { UploadForm } from "./upload-form";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

function fileOf(name: string, size: number): File {
  const file = new File(["x"], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("checkFile", () => {
  it.each([
    ["sales.csv", 10, null],
    ["sales.TSV", 10, null],
    ["book.xlsx", 10, null],
    ["old.xls", 10, "wrong_type"],
    ["notes.txt", 10, "wrong_type"],
    ["noextension", 10, "wrong_type"],
    ["empty.csv", 0, "empty"],
    ["huge.csv", MAX_UPLOAD_BYTES + 1, "too_large"],
  ])("%s (%i bytes) -> %s", (name, size, expected) => {
    expect(checkFile(fileOf(name, size))).toBe(expected);
  });

  it("suggests a name from the file", () => {
    expect(nameFromFile("retail_messy.csv")).toBe("retail_messy");
  });
});

describe("<UploadForm>", () => {
  it("states the size limit before upload", () => {
    renderWithProviders(<UploadForm />);
    expect(screen.getByText(/up to 50 MB/)).toBeInTheDocument();
  });

  it("explains a wrong file type", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderWithProviders(<UploadForm />);
    await user.upload(screen.getByTestId("file-input"), fileOf("notes.txt", 20));
    expect(await screen.findByRole("alert")).toHaveTextContent(/"notes.txt" isn't a CSV, TSV or .xlsx file/);
  });

  it("explains an oversized file with its size", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderWithProviders(<UploadForm />);
    await user.upload(screen.getByTestId("file-input"), fileOf("big.csv", MAX_UPLOAD_BYTES + 5 * 1024 * 1024));
    expect(await screen.findByRole("alert")).toHaveTextContent(/over the 50 MB limit/);
  });

  it("fills the dataset name from a valid file", async () => {
    const user = userEvent.setup();
    renderWithProviders(<UploadForm />);
    await user.upload(screen.getByTestId("file-input"), fileOf("retail_messy.csv", 2000));
    expect(screen.getByLabelText("Dataset name")).toHaveValue("retail_messy");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
