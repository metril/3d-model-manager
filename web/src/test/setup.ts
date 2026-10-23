import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";
import "@/i18n";

// Vitest isn't run with `test.globals: true`, so testing-library's
// auto-cleanup (which detects a global `afterEach`) never registers on its
// own — without this, DOM from one test leaks into the next.
afterEach(() => cleanup());
