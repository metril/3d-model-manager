import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpoolmanCard } from "@/components/settings/SpoolmanCard";

const { getMock, postMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SpoolmanCard />
    </QueryClientProvider>,
  );
}

describe("SpoolmanCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    putMock.mockReset();
    getMock.mockResolvedValue({ url: "http://sm:7912", mode: "mirror" });
  });

  it("seeds from settings and saves a mode change", async () => {
    putMock.mockResolvedValue({ url: "http://sm:7912", mode: "authoritative" });
    renderCard();
    const url = (await screen.findByLabelText("Server URL")) as HTMLInputElement;
    expect(url.value).toBe("http://sm:7912");
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "authoritative" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/spoolman/settings", {
        url: "http://sm:7912",
        mode: "authoritative",
      }),
    );
  });

  it("tests the connection and reports success and failure", async () => {
    postMock.mockResolvedValueOnce({ ok: true, version: "0.22" });
    renderCard();
    await screen.findByLabelText("Server URL");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connected \(Spoolman 0.22\)/)).toBeInTheDocument();
    postMock.mockResolvedValueOnce({ ok: false, error: "refused" });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connection failed: refused/)).toBeInTheDocument();
  });

  it("Sync now is disabled while off and runs when enabled", async () => {
    postMock.mockResolvedValue({ skipped: false, pulled: 3, errors: 0 });
    renderCard();
    const btn = await screen.findByRole("button", { name: "Sync now" });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(await screen.findByText(/Synced 3 spool/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/spoolman/sync");
  });

  it("disables Sync now when mode is off", async () => {
    getMock.mockResolvedValue({ url: null, mode: "off" });
    renderCard();
    expect(await screen.findByRole("button", { name: "Sync now" })).toBeDisabled();
  });
});
