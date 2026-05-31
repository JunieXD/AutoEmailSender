import { describe, expect, it } from 'vitest';
import { formatApiDateTime, parseApiDateTime } from './dateTime';

describe('dateTime', () => {
  it('parses api datetime without timezone suffix as utc', () => {
    expect(parseApiDateTime('2026-04-27T02:54:06').toISOString()).toBe(
      '2026-04-27T02:54:06.000Z',
    );
  });

  it('parses legacy space-separated api datetime without timezone suffix as utc', () => {
    expect(parseApiDateTime('2026-04-27 02:54:06').toISOString()).toBe(
      '2026-04-27T02:54:06.000Z',
    );
  });

  it('keeps explicit utc and offset strings as the same instant', () => {
    expect(parseApiDateTime('2026-04-27T02:54:06Z').toISOString()).toBe(
      '2026-04-27T02:54:06.000Z',
    );
    expect(parseApiDateTime('2026-04-27T10:54:06+08:00').toISOString()).toBe(
      '2026-04-27T02:54:06.000Z',
    );
  });

  it('formats api datetime to minute precision by default', () => {
    expect(formatApiDateTime('2026-04-27T02:54:06Z')).toMatch(/\d{2}:\d{2}$/);
  });

  it('formats api datetime to second precision when requested', () => {
    expect(
      formatApiDateTime('2026-04-27T02:54:06Z', {
        second: '2-digit',
      }),
    ).toMatch(/\d{2}:\d{2}:\d{2}$/);
  });
});