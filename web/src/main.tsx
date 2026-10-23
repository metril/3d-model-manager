import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ThemeProvider } from "next-themes";

import { Toaster } from "@/components/ui/sonner";
import { queryClient } from "@/queryClient";
import { AppearanceProvider } from "@/lib/appearance";
import { registerServiceWorker } from "@/lib/pwa";
import { router } from "@/router";

import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute={["class", "data-theme"]} defaultTheme="system" enableSystem>
        <AppearanceProvider>
          <RouterProvider router={router} />
          <Toaster />
        </AppearanceProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);

registerServiceWorker();
