import { useEffect } from 'react';

const blurFocusedNumberInputOnWheel = () => {
  const activeElement = document.activeElement;
  if (activeElement instanceof HTMLInputElement && activeElement.type === 'number') {
    activeElement.blur();
  }
};

export const usePreventNumberInputWheelChange = () => {
  useEffect(() => {
    window.addEventListener('wheel', blurFocusedNumberInputOnWheel, { capture: true });
    return () => {
      window.removeEventListener('wheel', blurFocusedNumberInputOnWheel, { capture: true });
    };
  }, []);
};
