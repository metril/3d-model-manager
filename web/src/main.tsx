import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ThemeProvider } from "next-themes";

import { AppProviders } from "@/AppProviders";
import { Toaster } from "@/components/ui/sonner";
import { queryClient } from "@/queryClient";
import { AppearanceProvider } from "@/lib/appearance";
import { installPreloadErrorReload } from "@/lib/preloadError";
import { registerServiceWorker } from "@/lib/pwa";
import { APP_THEME_KEY, migrateThemeKey } from "@/lib/selkiesTheme";
import { SelkiesThemeBridge } from "@/lib/SelkiesThemeBridge";
import { router } from "@/router";
import "@/i18n";

import "./index.css";

installPreloadErrorReload();

migrateThemeKey();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute={["class", "data-theme"]} defaultTheme="system" enableSystem storageKey={APP_THEME_KEY}>
        <SelkiesThemeBridge />
        <AppearanceProvider>
          <AppProviders>
            <RouterProvider router={router} />
          </AppProviders>
          <Toaster />
        </AppearanceProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);

registerServiceWorker();
