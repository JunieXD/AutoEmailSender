import { createHashHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { Shell } from "./components/shell";
import { MentorsPage, mentorSearch } from "./pages/mentors";

const rootRoute = createRootRoute({ component: Shell });

const mentorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: mentorSearch,
  component: MentorsPage,
});

// Hash URLs: the app is served as static files, which have nothing at deep paths.
export const router = createRouter({
  routeTree: rootRoute.addChildren([mentorsRoute]),
  history: createHashHistory(),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
