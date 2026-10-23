import { createHashHistory, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { Shell } from "./components/shell";
import { MentorPage } from "./pages/mentor";
import { MentorsPage, mentorSearch } from "./pages/mentors";
import { AboutSection, FIRST_SECTION, SettingsPage } from "./pages/settings";
import { TrashPage, trashSearch } from "./pages/trash";

/** The `phone` breakpoint in index.css. */
const PHONE = "(width <= 760px)";

const rootRoute = createRootRoute({ component: Shell });

// The mentor list is the home page. It lives under /mentors so that its navigation entry stays
// current on a mentor's page.
const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/mentors" });
  },
});

const mentorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "mentors",
  validateSearch: mentorSearch,
  component: MentorsPage,
});

// A fixed path, so it wins over a mentor's id.
const trashRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "mentors/trash",
  validateSearch: trashSearch,
  component: TrashPage,
});

const mentorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "mentors/$mentorId",
  params: {
    parse: ({ mentorId }) => ({ mentorId: Number(mentorId) }),
    stringify: ({ mentorId }) => ({ mentorId: String(mentorId) }),
  },
  component: MentorPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "settings",
  component: SettingsPage,
});

// docs/ui.md#页面: a phone lists the groups first; a wider window opens the first section.
const settingsHomeRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/",
  beforeLoad: () => {
    if (!matchMedia(PHONE).matches) throw redirect({ to: FIRST_SECTION });
  },
});

const aboutRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "about",
  component: AboutSection,
});

// Hash URLs: the app is served as static files, which have nothing at deep paths.
export const router = createRouter({
  routeTree: rootRoute.addChildren([
    homeRoute,
    mentorsRoute,
    trashRoute,
    mentorRoute,
    settingsRoute.addChildren([settingsHomeRoute, aboutRoute]),
  ]),
  history: createHashHistory(),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
