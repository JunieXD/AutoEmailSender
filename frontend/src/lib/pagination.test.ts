import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clampPageSize,
  getPageForPageSizeChange,
  getPageItems,
  getPaginationItems,
  getStoredPageSize,
  getTotalPages,
  PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  setStoredPageSize,
} from './pagination';

describe('pagination', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses ten items as the default page size', () => {
    expect(PAGE_SIZE).toBe(10);
  });

  it('defines supported fixed page size options', () => {
    expect(PAGE_SIZE_OPTIONS).toEqual([10, 20, 50]);
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

  it('uses the last available page when a collection shrinks', () => {
    const items = Array.from({ length: 11 }, (_, index) => index + 1);

    expect(getPageItems(items, 99)).toEqual([11]);
  });

  it('keeps the first visible item when the page size changes', () => {
    expect(
      getPageForPageSizeChange({
        page: 3,
        pageSize: 10,
        nextPageSize: 20,
        totalCount: 95,
      }),
    ).toBe(2);
    expect(
      getPageForPageSizeChange({
        page: 4,
        pageSize: 20,
        nextPageSize: 10,
        totalCount: 95,
      }),
    ).toBe(7);
  });

  it('builds stable numbered-page windows with first and last pages', () => {
    expect(getPaginationItems(1, 4)).toEqual([1, 2, 3, 4]);
    expect(getPaginationItems(1, 20)).toEqual([
      1,
      2,
      3,
      4,
      5,
      'ellipsis-end',
      20,
    ]);
    expect(getPaginationItems(10, 20)).toEqual([
      1,
      'ellipsis-start',
      9,
      10,
      11,
      'ellipsis-end',
      20,
    ]);
    expect(getPaginationItems(20, 20)).toEqual([
      1,
      'ellipsis-start',
      16,
      17,
      18,
      19,
      20,
    ]);
  });

  it('normalizes invalid pagination inputs safely', () => {
    expect(getTotalPages(Number.NaN, 0)).toBe(1);
    expect(getPageItems([1, 2, 3], Number.NaN, 0)).toEqual([1]);
    expect(getPaginationItems(Number.NaN, Number.POSITIVE_INFINITY)).toEqual([1]);
  });

  it('normalizes custom page sizes to the supported range', () => {
    expect(clampPageSize(0)).toBe(1);
    expect(clampPageSize(12)).toBe(12);
    expect(clampPageSize(101)).toBe(100);
    expect(clampPageSize(Number.NaN)).toBe(PAGE_SIZE);
    expect(clampPageSize(Number.NaN, Number.NaN)).toBe(PAGE_SIZE);
    expect(clampPageSize(Number.NaN, 200)).toBe(100);
  });

  it('reads and writes stored page size with fallback for invalid values', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        values.set(key, value);
      }),
    });

    expect(getStoredPageSize('page-size:test')).toBe(PAGE_SIZE);

    setStoredPageSize('page-size:test', 20);
    expect(getStoredPageSize('page-size:test')).toBe(20);

    values.set('page-size:test', '200');
    expect(getStoredPageSize('page-size:test')).toBe(PAGE_SIZE);
  });
});
