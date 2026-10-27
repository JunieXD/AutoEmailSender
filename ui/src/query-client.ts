import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { isSignedOut } from "@/api/call";

// Wherever the server turns the login away, every place that needs an account then reads it as
// expired and offers logging in again.
function recheckAccount(error: unknown) {
  if (isSignedOut(error)) void queryClient.invalidateQueries({ queryKey: ["getAccount"] });
}

// Everything goes to the local core: retrying at once rarely helps and only delays the error, and
// whether the computer is online says nothing about whether a call can run.
export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: recheckAccount }),
  mutationCache: new MutationCache({ onError: recheckAccount }),
  defaultOptions: {
    queries: { retry: false, networkMode: "always" },
    mutations: { networkMode: "always" },
  },
});
