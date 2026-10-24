import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChannelModal } from "@/components/settings/notifications/ChannelModal";

import { catalog, fakeChannel, renderWithQuery } from "./testUtils";

const { postMock, patchMock } = vi.hoisted(() => ({ postMock: vi.fn(), patchMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, patch: patchMock } };
});

describe("ChannelModal", () => {
  beforeEach(() => {
    postMock.mockReset();
    patchMock.mockReset();
  });

  it("renders kind fields dynamically and creates a channel", async () => {
    postMock.mockResolvedValue(fakeChannel());
    const onClose = vi.fn();
    renderWithQuery(<ChannelModal catalog={catalog} onClose={onClose} />);
    expect(screen.getByLabelText("Server URL")).toHaveValue("https://ntfy.sh");
    expect(screen.getByLabelText("Attach camera photo")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Phone" } });
    fireEvent.click(screen.getByLabelText("Print finished"));
    fireEvent.click(screen.getByRole("button", { name: "Create channel" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/notifications/channels",
        expect.objectContaining({
          kind: "ntfy",
          name: "Phone",
          config: { url: "https://ntfy.sh" },
          events: ["print_done"],
          quiet_hours: null,
          digest_cron: null,
        }),
      ),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("hides the photo option for kinds without photo support", () => {
    renderWithQuery(<ChannelModal catalog={catalog} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "webhook" } });
    expect(screen.queryByLabelText("Attach camera photo")).not.toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toBeInTheDocument();
  });

  it("echoes masked secrets back unchanged on edit and sends quiet hours", async () => {
    patchMock.mockResolvedValue(fakeChannel());
    renderWithQuery(<ChannelModal catalog={catalog} channel={fakeChannel()} onClose={vi.fn()} />);
    expect(screen.getByLabelText("Token")).toHaveValue("********");
    fireEvent.click(screen.getByLabelText("Quiet hours"));
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "23:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith(
        "/notifications/channels/1",
        expect.objectContaining({
          config: { url: "https://ntfy.sh", token: "********" },
          quiet_hours: { start: "23:00", end: "07:00", tz: "UTC" },
        }),
      ),
    );
  });
});
