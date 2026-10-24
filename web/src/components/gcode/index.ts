import { createElement, lazy, Suspense, type ComponentProps } from "react";
import type GcodeToolpathViewer from "./GcodeToolpathViewer";

const Inner = lazy(() => import("./GcodeToolpathViewer"));

/** Lazy wrapper: three.js + renderer load only when a toolpath is actually shown. */
export function LazyGcodeToolpathViewer(props: ComponentProps<typeof GcodeToolpathViewer>) {
  return createElement(
    Suspense,
    {
      fallback: createElement("div", {
        className: `flex items-center justify-center text-sm text-muted-foreground ${props.className ?? ""}`,
        role: "status",
      }, "…"),
    },
    createElement(Inner, props),
  );
}
export type { GcodeToolpathViewerProps, GcodeLoadedMeta } from "./GcodeToolpathViewer";
