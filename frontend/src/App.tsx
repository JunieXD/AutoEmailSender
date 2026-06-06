import { lazy, Suspense } from 'react';
import {
  createBrowserRouter,
  createHashRouter,
  Outlet,
  RouterProvider,
} from 'react-router-dom';
import { DesktopStartupStatusBanner } from '@/components/organisms/DesktopStartupStatusBanner';
import { RouteScrollRestoration } from '@/components/organisms/RouteScrollRestoration';
import { TopNavBar } from '@/components/organisms/TopNavBar';
import { DesktopBackendProvider } from '@/context/DesktopBackendContext';
import { NotificationProvider } from '@/context/NotificationContext';
import { SelectionProvider } from '@/context/SelectionContext';
import { WorkspaceDraftGuardProvider } from '@/context/WorkspaceDraftGuardContext';

const CreateTaskPage = lazy(() =>
  import('@/pages/CreateTaskPage').then((module) => ({ default: module.CreateTaskPage })),
);
const HomePage = lazy(() => import('@/pages/HomePage').then((module) => ({ default: module.HomePage })));
const NotFoundPage = lazy(() =>
  import('@/pages/NotFoundPage').then((module) => ({ default: module.NotFoundPage })),
);
const ProfessorsPage = lazy(() =>
  import('@/pages/ProfessorsPage').then((module) => ({ default: module.ProfessorsPage })),
);
const DashboardPage = lazy(() =>
  import('@/pages/DashboardPage').then((module) => ({ default: module.DashboardPage })),
);
const ProfilePage = lazy(() =>
  import('@/pages/ProfilePage').then((module) => ({ default: module.ProfilePage })),
);
const TasksPage = lazy(() => import('@/pages/TasksPage').then((module) => ({ default: module.TasksPage })));
const TestComposePage = lazy(() =>
  import('@/pages/TestComposePage').then((module) => ({ default: module.TestComposePage })),
);
const WorkspacePage = lazy(() =>
  import('@/pages/WorkspacePage').then((module) => ({ default: module.WorkspacePage })),
);

const routeLoadingFallback = (
  <div className="flex min-h-[16rem] items-center justify-center text-sm text-muted-foreground">
    页面加载中…
  </div>
);

const AppShell = () => (
  <>
    <RouteScrollRestoration />
    <NotificationProvider>
      <DesktopBackendProvider>
        <WorkspaceDraftGuardProvider>
          <SelectionProvider>
            <div className="flex min-h-screen flex-col bg-background">
              <DesktopStartupStatusBanner />
              <TopNavBar />
              <div className="min-h-0 flex-1">
                <Suspense fallback={routeLoadingFallback}>
                  <Outlet />
                </Suspense>
              </div>
            </div>
          </SelectionProvider>
        </WorkspaceDraftGuardProvider>
      </DesktopBackendProvider>
    </NotificationProvider>
  </>
);

const routes = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'professors', element: <ProfessorsPage /> },
      { path: 'tasks', element: <TasksPage /> },
      { path: 'create-task', element: <CreateTaskPage /> },
      { path: 'test-compose', element: <TestComposePage /> },
      { path: 'workspace/:id', element: <WorkspacePage /> },
      { path: 'profile', element: <ProfilePage /> },
      { path: '404', element: <NotFoundPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

function App() {
  const router = window.autoEmailSender
    ? createHashRouter(routes)
    : createBrowserRouter(routes);

  return <RouterProvider router={router} />;
}

export default App;
