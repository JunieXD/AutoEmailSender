import { useCallback, useRef, useState } from "react";
import {
  ConfirmDialog,
  type ConfirmDialogTone,
} from "@/components/atoms/ConfirmDialog";

type ConfirmOptions = {
  title: string;
  description?: string;
  confirmLabel?: string;
  secondaryLabel?: string;
  cancelLabel?: string | null;
  confirmationCheckbox?: {
    label: string;
    onConfirmChecked: () => void;
  };
  tone?: ConfirmDialogTone;
};

type ConfirmState = ConfirmOptions & {
  confirmationCheckboxChecked: boolean;
  open: boolean;
};

export const useConfirmDialog = () => {
  const [state, setState] = useState<ConfirmState | null>(null);
  const resolverRef = useRef<((value: boolean) => void) | null>(null);
  const actionResolverRef = useRef<((value: string) => void) | null>(null);

  const closeActionDialog = useCallback((
    value: string,
    confirmationCheckboxChecked = false,
    confirmationCheckbox?: ConfirmOptions["confirmationCheckbox"],
  ) => {
    if (value === "confirm" && confirmationCheckboxChecked) {
      confirmationCheckbox?.onConfirmChecked();
    }
    resolverRef.current?.(value === "confirm");
    resolverRef.current = null;
    actionResolverRef.current?.(value);
    actionResolverRef.current = null;
    setState(null);
  }, []);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      actionResolverRef.current = null;
      setState({
        ...options,
        confirmationCheckboxChecked: false,
        open: true,
      });
    });
  }, []);

  const choose = useCallback((options: ConfirmOptions) => {
    return new Promise<string>((resolve) => {
      resolverRef.current = null;
      actionResolverRef.current = resolve;
      setState({
        ...options,
        confirmationCheckboxChecked: false,
        open: true,
      });
    });
  }, []);

  const dialog = state ? (
    <ConfirmDialog
      open={state.open}
      title={state.title}
      description={state.description}
      confirmLabel={state.confirmLabel}
      secondaryLabel={state.secondaryLabel}
      cancelLabel={state.cancelLabel}
      confirmationCheckboxLabel={state.confirmationCheckbox?.label}
      confirmationCheckboxChecked={state.confirmationCheckboxChecked}
      tone={state.tone}
      onCancel={() => closeActionDialog("cancel")}
      onConfirm={() =>
        closeActionDialog(
          "confirm",
          state.confirmationCheckboxChecked,
          state.confirmationCheckbox,
        )
      }
      onConfirmationCheckboxChange={(checked) =>
        setState((current) =>
          current ? { ...current, confirmationCheckboxChecked: checked } : current,
        )
      }
      onSecondary={
        state.secondaryLabel
          ? () => closeActionDialog("secondary")
          : undefined
      }
    />
  ) : null;

  return {
    confirm,
    choose,
    dialog,
  };
};
