import { StatsPage } from "./pages/stats";
import { FindPage, findSearch } from "./pages/find";
import { createHashHistory, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { call } from "./api/call";
import { queryClient } from "./query-client";
import { TasksPage, TaskReviewPage, tasksSearch, taskSearch } from "./pages/tasks";
import { ConversationsPage, conversationSearch } from "./pages/conversations";
import { Shell, MorePage } from "./components/shell";
import { MentorPage } from "./pages/mentor";
import { MentorsPage, mentorSearch } from "./pages/mentors";
import { MailboxesSection } from "./pages/mailboxes";
import { ModelsSection } from "./pages/models";
import { MaterialsSection } from "./pages/materials";
import { ProfileSection } from "./pages/profile";
import { TemplatesSection } from "./pages/templates";
import { LetterStyleSection } from "./pages/letter-style";
import {
  AboutSection,
  AppearanceSection,
  BackgroundSection,
  ChecksSection,
  NotificationsSection,
  ReplyProcessingSection,
  SettingsPage,
} from "./pages/settings";
import { TrashPage, trashSearch } from "./pages/trash";
import { AgentSection } from "./pages/agent";
import { BackupSection } from "./pages/backup";
import { WelcomePage } from "./pages/welcome";

const rootRoute = createRootRoute({ component: Shell });

// The mentor list is the home page. It lives under /mentors so that its navigation entry stays
// current on a mentor's page. Until the first-run guide is over, the app opens on it instead.
const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: async () => {
    const profile = await queryClient.ensureQueryData({
      queryKey: ["getProfile"],
      queryFn: () => call("getProfile", {}),
    });
    throw redirect({ to: profile.onboarded ? "/mentors" : "/welcome" });
  },
});

const welcomeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "welcome",
  component: WelcomePage,
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

const conversationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "conversations",
  validateSearch: conversationSearch,
  component: ConversationsPage,
});

const findRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "find",
  validateSearch: findSearch,
  component: FindPage,
});
const statsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "stats",
  component: StatsPage,
});
const moreRoute = createRoute({ getParentRoute: () => rootRoute, path: "more", component: MorePage });
const tasksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "tasks",
  validateSearch: tasksSearch,
  component: TasksPage,
});
const taskRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "tasks/$id",
  params: { parse: ({ id }) => ({ id: Number(id) }), stringify: ({ id }) => ({ id: String(id) }) },
  validateSearch: taskSearch,
  component: TaskReviewPage,
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

const checksRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "checks",
  component: ChecksSection,
});

const modelsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "models",
  component: ModelsSection,
});

const repliesRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "replies",
  component: ReplyProcessingSection,
});

const appearanceRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "appearance",
  component: AppearanceSection,
});

const notificationsRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "notifications",
  component: NotificationsSection,
});

const backgroundRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "background",
  component: BackgroundSection,
});

const backupRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "backup",
  component: BackupSection,
});

const agentRoute = createRoute({
  getParentRoute: () => settingsRoute,
  path: "agent",
  component: AgentSection,
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
    welcomeRoute,
    mentorsRoute,
    trashRoute,
    mentorRoute,
    conversationsRoute,
    moreRoute,
    statsRoute,
    findRoute,
    tasksRoute,
    taskRoute,
    settingsRoute.addChildren([
      settingsHomeRoute,
      profileRoute,
      materialsRoute,
      templatesRoute,
      letterStyleRoute,
      mailboxesRoute,
      checksRoute,
      modelsRoute,
      repliesRoute,
      appearanceRoute,
      notificationsRoute,
      backgroundRoute,
      backupRoute,
      agentRoute,
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
