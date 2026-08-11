import { lazy, Suspense } from 'react';
import {
  createBrowserRouter,
  createHashRouter,
  Outlet,
  RouterProvider,
} from 'react-router-dom';
import { DesktopStartupStatusBanner } from '@/components/organisms/DesktopStartupStatusBanner';
import { AgentSupportOnboarding } from '@/components/organisms/AgentSupportOnboarding';
import { KeepAliveLayout } from '@/components/organisms/KeepAliveLayout';
import { RouteScrollRestoration } from '@/components/organisms/RouteScrollRestoration';
import { TopNavBar } from '@/components/organisms/TopNavBar';
import { BackgroundTaskNotificationProvider } from '@/app/providers/BackgroundTaskNotificationContext';
import { DesktopBackendProvider } from '@/context/DesktopBackendContext';
import { NotificationProvider } from '@/context/NotificationContext';
import { SelectionProvider } from '@/context/SelectionContext';
import { WorkspaceDraftGuardProvider } from '@/context/WorkspaceDraftGuardContext';
import { AgentUiHandoffProvider } from '@/features/agent-ui-handoffs/AgentUiHandoffProvider';
import { usePreventNumberInputWheelChange } from '@/lib/usePreventNumberInputWheelChange';

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
const CommunityMentorsPage = lazy(() =>
  import('@/pages/CommunityMentorsPage').then((module) => ({
    default: module.CommunityMentorsPage,
  })),
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
      <BackgroundTaskNotificationProvider>
        <DesktopBackendProvider>
          <WorkspaceDraftGuardProvider>
            <SelectionProvider>
              <AgentUiHandoffProvider>
                <div
                  data-app-shell="true"
                  className="flex h-dvh min-h-0 flex-col overflow-hidden bg-background"
                >
                  <DesktopStartupStatusBanner />
                  <AgentSupportOnboarding />
                  <TopNavBar />
                  <div
                    data-app-scroll-container="true"
                    className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain"
                  >
                    <Suspense fallback={routeLoadingFallback}>
                      <Outlet />
                    </Suspense>
                  </div>
                </div>
              </AgentUiHandoffProvider>
            </SelectionProvider>
          </WorkspaceDraftGuardProvider>
        </DesktopBackendProvider>
      </BackgroundTaskNotificationProvider>
    </NotificationProvider>
  </>
);

const routes = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      // 所有路由都挂在 KeepAliveLayout 之下：保活路由进 KeepAlive 缓存，非保活路由旁路渲染。
      // 关键点：KeepAliveLayout 永不卸载，<KeepAlive> 内部的 useState<CacheNode[]> 因此始终
      // 存活——这是"非保活页面回到保活页面也不丢 state"的前提。
      {
        element: <KeepAliveLayout />,
        children: [
          { index: true, element: <HomePage /> },
          { path: 'dashboard', element: <DashboardPage /> },
          { path: 'professors', element: <ProfessorsPage /> },
          { path: 'community', element: <CommunityMentorsPage /> },
          { path: 'tasks', element: <TasksPage /> },
          { path: 'profile', element: <ProfilePage /> },
          { path: 'create-task', element: <CreateTaskPage /> },
          { path: 'test-compose', element: <TestComposePage /> },
          { path: 'workspace/:id', element: <WorkspacePage /> },
          { path: '404', element: <NotFoundPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

function App() {
  usePreventNumberInputWheelChange();
  const router = window.autoEmailSender
    ? createHashRouter(routes)
    : createBrowserRouter(routes);

  return <RouterProvider router={router} />;
}

export default App;
