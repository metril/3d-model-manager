import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrintersOverviewOut } from "@/api/types";
import { a1Mini } from "@/components/print/card/testUtils";

import { DEV_MODE_WIKI, DevModeBanner } from "./DevModeBanner";

const { box } = vi.hoisted(() => ({
  box: { features: { printer_enabled: true }, warnings: [] as unknown[], overview: { printers: [] } as unknown },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (url: string) => {
        if (url === "/features") return Promise.resolve(box.features);
        if (url === "/printers/developer-mode-warnings") return Promise.resolve(box.warnings);
        if (url === "/printers/overview") return Promise.resolve(box.overview as PrintersOverviewOut);
        return Promise.reject(new Error(url));
      },
    },
  };
});

function renderBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DevModeBanner />
    </QueryClientProvider>,
  );
}

describe("DevModeBanner", () => {
  beforeEach(() => {
    sessionStorage.clear();
    box.features = { printer_enabled: true };
    box.warnings = [];
    box.overview = { printers: [] };
  });

  it("renders nothing when every printer is fine", async () => {
    renderBanner();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("dev-mode-banner-off")).toBeNull();
    expect(screen.queryByTestId("dev-mode-banner-unknown")).toBeNull();
  });

  it("warns with printer names and links to the wiki", async () => {
    box.warnings = [{ printer_id: 1, name: "A1 Mini", reason: "verify failed" }, { printer_id: 2, name: "X1C", reason: null }];
    renderBanner();
    const banner = await screen.findByTestId("dev-mode-banner-off");
    expect(banner).toHaveTextContent("Developer LAN mode is not enabled on: A1 Mini, X1C. Some features may not work.");
    expect(screen.getByRole("link", { name: "How to enable it" })).toHaveAttribute("href", DEV_MODE_WIKI);
  });

  it("dismiss hides it for the session", async () => {
    box.warnings = [{ printer_id: 1, name: "A1 Mini", reason: null }];
    const { unmount } = renderBanner();
    await screen.findByTestId("dev-mode-banner-off");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("dev-mode-banner-off")).toBeNull();
    unmount();
    renderBanner();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("dev-mode-banner-off")).toBeNull();
  });

  it("re-shows when a different printer is affected", async () => {
    sessionStorage.setItem("devModeBannerDismissed", JSON.stringify(["off:A1 Mini"]));
    box.warnings = [{ printer_id: 1, name: "A1 Mini", reason: null }, { printer_id: 2, name: "P1S", reason: null }];
    renderBanner();
    expect(await screen.findByTestId("dev-mode-banner-off")).toBeInTheDocument();
  });

  it("grey unknown variant for an online printer with dev_mode null", async () => {
    box.overview = { printers: [a1Mini({}, { dev_mode: null }), a1Mini({}, { dev_mode: true })] };
    renderBanner();
    const banner = await screen.findByTestId("dev-mode-banner-unknown");
    expect(banner).toHaveTextContent("not known yet for: A1 Mini");
  });

  it("no unknown banner for offline printers or when the printer feature is off", async () => {
    box.overview = { printers: [a1Mini({}, { dev_mode: null, online: false })] };
    renderBanner();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("dev-mode-banner-unknown")).toBeNull();
    box.features = { printer_enabled: false };
    box.warnings = [{ printer_id: 1, name: "A1 Mini", reason: null }];
    renderBanner();
    await waitFor(() => expect(screen.queryByTestId("dev-mode-banner-off")).toBeNull());
  });
});
