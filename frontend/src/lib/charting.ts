export const dashboardPieColors = [
  '#14b8a6',
  '#f97316',
  '#3b82f6',
  '#84cc16',
  '#8b5cf6',
  '#ef4444',
  '#06b6d4',
  '#eab308',
  '#ec4899',
  '#22c55e',
  '#6366f1',
  '#f59e0b',
  '#0ea5e9',
  '#a855f7',
  '#10b981',
  '#f43f5e',
  '#0891b2',
  '#65a30d',
  '#2563eb',
  '#d946ef',
  '#dc2626',
  '#0d9488',
  '#ca8a04',
  '#7c3aed',
  '#16a34a',
  '#ea580c',
  '#0284c7',
  '#db2777',
  '#4f46e5',
  '#059669',
  '#b45309',
  '#be123c',
  '#2dd4bf',
  '#9333ea',
  '#4d7c0f',
  '#fb7185',
] as const;

type HslColor = {
  hue: number;
  saturation: number;
  lightness: number;
};

const normalizeHexColor = (hex: string): string => hex.trim().toLowerCase();

const hexToRgb = (hex: string): { red: number; green: number; blue: number } => {
  const normalized = normalizeHexColor(hex).replace('#', '');
  const red = Number.parseInt(normalized.slice(0, 2), 16);
  const green = Number.parseInt(normalized.slice(2, 4), 16);
  const blue = Number.parseInt(normalized.slice(4, 6), 16);
  return { red, green, blue };
};

const hexToHsl = (hex: string): HslColor => {
  const { red, green, blue } = hexToRgb(hex);
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;

  if (max === min) {
    return { hue: 0, saturation: 0, lightness };
  }

  const delta = max - min;
  const saturation = lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min);
  let hue = 0;

  if (max === r) {
    hue = (g - b) / delta + (g < b ? 6 : 0);
  } else if (max === g) {
    hue = (b - r) / delta + 2;
  } else {
    hue = (r - g) / delta + 4;
  }

  return { hue: hue * 60, saturation, lightness };
};

const getHueDistance = (firstHue: number, secondHue: number): number => {
  const distance = Math.abs(firstHue - secondHue);
  return Math.min(distance, 360 - distance);
};

export const arePieSliceColorsSimilar = (firstColor: string, secondColor: string): boolean => {
  const first = normalizeHexColor(firstColor);
  const second = normalizeHexColor(secondColor);

  if (first === second) {
    return true;
  }

  const firstHsl = hexToHsl(first);
  const secondHsl = hexToHsl(second);
  const hueDistance = getHueDistance(firstHsl.hue, secondHsl.hue);
  const saturationDistance = Math.abs(firstHsl.saturation - secondHsl.saturation);
  const lightnessDistance = Math.abs(firstHsl.lightness - secondHsl.lightness);

  return hueDistance < 22 && saturationDistance < 0.2 && lightnessDistance < 0.16;
};

const resolveDistinctPieColor = (
  preferredIndex: number,
  previousColor: string | null,
  firstColor: string | null,
): string => {
  for (let offset = 0; offset < dashboardPieColors.length; offset += 1) {
    const candidate = dashboardPieColors[(preferredIndex + offset) % dashboardPieColors.length];
    const conflictsWithPrevious =
      previousColor !== null && arePieSliceColorsSimilar(candidate, previousColor);
    const conflictsWithFirst = firstColor !== null && arePieSliceColorsSimilar(candidate, firstColor);

    if (!conflictsWithPrevious && !conflictsWithFirst) {
      return candidate;
    }
  }

  return dashboardPieColors[preferredIndex % dashboardPieColors.length];
};

export const assignPieSliceColors = (count: number): string[] => {
  if (count <= 0) {
    return [];
  }

  const colors: string[] = [];

  for (let index = 0; index < count; index += 1) {
    const previousColor = index > 0 ? colors[index - 1] : null;
    const firstColor = index === count - 1 && count > 1 ? colors[0] : null;
    colors.push(resolveDistinctPieColor(index, previousColor, firstColor));
  }

  return colors;
};
