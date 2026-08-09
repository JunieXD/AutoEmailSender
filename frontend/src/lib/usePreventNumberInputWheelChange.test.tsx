import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { usePreventNumberInputWheelChange } from './usePreventNumberInputWheelChange';

const NumberInputHarness = () => {
  usePreventNumberInputWheelChange();
  return <input aria-label="发送数量" type="number" defaultValue="30" />;
};

describe('usePreventNumberInputWheelChange', () => {
  it('blurs a focused number input before wheel scrolling can change it', () => {
    render(<NumberInputHarness />);
    const input = screen.getByRole('spinbutton', { name: '发送数量' });

    input.focus();
    expect(input).toHaveFocus();

    fireEvent.wheel(input, { deltaY: 100 });

    expect(input).not.toHaveFocus();
    expect(input).toHaveValue(30);
  });
});
