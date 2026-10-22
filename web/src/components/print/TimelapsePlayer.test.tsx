import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TimelapsePlayer } from "@/components/print/TimelapsePlayer";

describe("TimelapsePlayer", () => {
  it("renders a video with the archive's timelapse src", () => {
    render(<TimelapsePlayer archiveId={42} />);

    const video = document.querySelector("video");
    expect(video).toHaveAttribute("src", "/api/archives/42/timelapse");
    expect(video).toHaveAttribute("controls");
    expect(video).toHaveAttribute("playsinline");
  });

  it("uses the poster when given", () => {
    render(<TimelapsePlayer archiveId={42} poster="/api/archives/42/snapshot" />);

    expect(document.querySelector("video")).toHaveAttribute("poster", "/api/archives/42/snapshot");
  });

  it("shows a download link when it can't play in this browser", () => {
    render(<TimelapsePlayer archiveId={42} />);

    fireEvent.error(document.querySelector("video")!);

    expect(screen.getByText("Can't play in this browser")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Download" });
    expect(link).toHaveAttribute("href", "/api/archives/42/timelapse");
  });
});
