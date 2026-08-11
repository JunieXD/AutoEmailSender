export const PACKAGED_QA_GRACEFUL_QUIT_MESSAGE = 0x84a5;

export function shouldRegisterPackagedQaGracefulQuit(input: {
  platform: NodeJS.Platform;
  activeUserDataPath: string | null;
}): boolean {
  return input.platform === "win32" && input.activeUserDataPath !== null;
}
