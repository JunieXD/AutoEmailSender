import { app } from "electron";
import { configurePackagedQaUserData } from "./main/packaged-qa/user-data.js";

try {
  configurePackagedQaUserData(app);
  void import("./main/bootstrap/application.js")
    .then(({ bootstrapDesktopApplication }) => {
      bootstrapDesktopApplication();
    })
    .catch((error: unknown) => {
      console.error("Unable to bootstrap the desktop application:", error);
      app.exit(1);
    });
} catch (error) {
  console.error("Packaged QA startup gate rejected the launch:", error);
  app.exit(1);
}
