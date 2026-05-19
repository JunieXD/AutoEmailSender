import { describe, expect, it } from 'vitest';
import { getPageItems, getTotalPages, PAGE_SIZE } from './pagination';

describe('pagination', () => {
  it('uses ten items as the default page size', () => {
    expect(PAGE_SIZE).toBe(10);
  });

  it('returns at least one page for empty collections', () => {
    expect(getTotalPages(0)).toBe(1);
  });

  it('calculates total pages with the default page size', () => {
    expect(getTotalPages(PAGE_SIZE)).toBe(1);
    expect(getTotalPages(PAGE_SIZE + 1)).toBe(2);
  });

  it('returns the requested page slice', () => {
    const items = Array.from({ length: PAGE_SIZE + 3 }, (_, index) => index + 1);

    expect(getPageItems(items, 1)).toEqual(items.slice(0, PAGE_SIZE));
    expect(getPageItems(items, 2)).toEqual(items.slice(PAGE_SIZE));
  });
});
