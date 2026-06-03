import { render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceDraftGuardProvider } from "@/context/WorkspaceDraftGuardContext";
import { useWorkspaceDraftGuard } from "@/context/useWorkspaceDraftGuard";


const Harness = ({
  guard,
  onResults,
}: {
  guard: () => Promise<boolean>;
  onResults: (results: Promise<boolean>[]) => void;
}) => {
  const {
    registerWorkspaceDraftGuard,
    requestWorkspaceDraftGuard,
  } = useWorkspaceDraftGuard();

  useEffect(() => registerWorkspaceDraftGuard(guard), [guard, registerWorkspaceDraftGuard]);

  return (
    <button
      type="button"
      onClick={() => {
        onResults([
          requestWorkspaceDraftGuard(),
          requestWorkspaceDraftGuard(),
        ]);
      }}
    >
      request
    </button>
  );
};

describe("WorkspaceDraftGuardProvider", () => {
  it("reuses an in-flight draft guard request", async () => {
    let resolveGuard: (value: boolean) => void = () => undefined;
    const guard = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveGuard = resolve;
        }),
    );
    const onResults = vi.fn();

    render(
      <WorkspaceDraftGuardProvider>
        <Harness guard={guard} onResults={onResults} />
      </WorkspaceDraftGuardProvider>,
    );

    screen.getByRole("button", { name: "request" }).click();

    await waitFor(() => {
      expect(onResults).toHaveBeenCalled();
    });
    expect(guard).toHaveBeenCalledTimes(1);
    const [first, second] = onResults.mock.calls[0][0] as Promise<boolean>[];
    expect(first).toBe(second);

    resolveGuard(true);
    await expect(first).resolves.toBe(true);
    await expect(second).resolves.toBe(true);
  });
});