import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { useConfirmDialog } from './useConfirmDialog';

const ConfirmHarness = ({ onRemember }: { onRemember: () => void }) => {
  const { confirm, dialog } = useConfirmDialog();
  const [result, setResult] = useState('pending');

  return (
    <>
      <button
        type="button"
        onClick={() => {
          void confirm({
            title: '附件超过 1 MB',
            confirmationCheckbox: {
              label: '我已知晓，不再提示',
              onConfirmChecked: onRemember,
            },
          }).then((confirmed) => setResult(confirmed ? 'confirmed' : 'canceled'));
        }}
      >
        打开确认
      </button>
      <output>{result}</output>
      {dialog}
    </>
  );
};

describe('useConfirmDialog', () => {
  it('remembers the checkbox only after the primary action is confirmed', async () => {
    const onRemember = vi.fn();
    render(<ConfirmHarness onRemember={onRemember} />);

    fireEvent.click(screen.getByRole('button', { name: '打开确认' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '我已知晓，不再提示' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(screen.getByText('canceled')).toBeInTheDocument());
    expect(onRemember).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '打开确认' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '我已知晓，不再提示' }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => expect(screen.getByText('confirmed')).toBeInTheDocument());
    expect(onRemember).toHaveBeenCalledTimes(1);
  });
});
