import { createHashHistory, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { Shell } from "./components/shell";
import { MentorPage } from "./pages/mentor";
import { MentorsPage, mentorSearch } from "./pages/mentors";
import { MailboxesSection } from "./pages/mailboxes";
import { ModelsSection } from "./pages/models";
import { MaterialsSection } from "./pages/materials";
import { ProfileSection } from "./pages/profile";
import { TemplatesSection } from "./pages/templates";
import { LetterStyleSection } from "./pages/letter-style";
import { AboutSection, SettingsPage } from "./pages/settings";
import { TrashPage, trashSearch } from "./pages/trash";

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

// The bare /settings: the group list on a phone; SettingsPage moves a wider window on to a section.
const settingsHomeRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "/",
});

const profileRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "profile",
  component: ProfileSection,
});

const materialsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "materials",
  component: MaterialsSection,
});

const templatesRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "templates",
  component: TemplatesSection,
});

const letterStyleRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "letter-style",
  component: LetterStyleSection,
});

const mailboxesRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "mailboxes",
  component: MailboxesSection,
});

const modelsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "models",
  component: ModelsSection,
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
    settingsRoute.addChildren([
      settingsHomeRoute,
      profileRoute,
      materialsRoute,
      templatesRoute,
      letterStyleRoute,
      mailboxesRoute,
      modelsRoute,
      aboutRoute,
    ]),
  ]),
  history: createHashHistory(),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
