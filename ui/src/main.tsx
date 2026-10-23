import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { router } from "./router";
import "./index.css";

// Everything goes to the local core: retrying at once rarely helps and only delays the error, and
// whether the computer is online says nothing about whether a call can run.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, networkMode: "always" },
    mutations: { networkMode: "always" },
  },
});

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
