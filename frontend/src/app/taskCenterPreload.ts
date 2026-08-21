type TasksPageModule = typeof import("@/pages/TasksPage");
type BackgroundTasksPageModule = typeof import("@/pages/BackgroundTasksPage");

let tasksPagePromise: Promise<TasksPageModule> | null = null;
let backgroundTasksPagePromise: Promise<BackgroundTasksPageModule> | null = null;

export const loadTasksPage = (): Promise<TasksPageModule> => {
  if (tasksPagePromise === null) {
    tasksPagePromise = import("@/pages/TasksPage").catch((error: unknown) => {
      tasksPagePromise = null;
      throw error;
    });
  }
  return tasksPagePromise;
};

export const loadBackgroundTasksPage = (): Promise<BackgroundTasksPageModule> => {
  if (backgroundTasksPagePromise === null) {
    backgroundTasksPagePromise = import("@/pages/BackgroundTasksPage").catch(
      (error: unknown) => {
        backgroundTasksPagePromise = null;
        throw error;
      },
    );
  }
  return backgroundTasksPagePromise;
};

export const preloadTaskCenter = async (): Promise<void> => {
  await Promise.all([loadTasksPage(), loadBackgroundTasksPage()]);
};
