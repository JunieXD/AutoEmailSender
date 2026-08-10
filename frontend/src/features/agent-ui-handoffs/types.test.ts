import { describe, expect, it } from 'vitest';
import type { DesktopAgentUiHandoff } from '@/types/desktop';
import { validateAgentUiHandoff } from './types';

const buildHandoff = (
  overrides: Partial<DesktopAgentUiHandoff> = {},
): DesktopAgentUiHandoff => ({
  handoffId: 'uih_test',
  schemaVersion: 1,
  surface: 'professors.management',
  route: '/professors',
  status: 'claimed',
  selectionCount: 2,
  selectionFingerprint: 'fingerprint',
  uiEffects: ['focus_window', 'navigate', 'replace_selection'],
  result: null,
  failureMessage: null,
  deliveryAttempts: 1,
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
  claimedAt: new Date().toISOString(),
  awaitingUserAt: null,
  appliedAt: null,
  failedAt: null,
  canceledAt: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  availableActions: ['read', 'wait', 'cancel'],
  consumerId: 'desktop:test',
  claimExpiresAt: new Date(Date.now() + 30_000).toISOString(),
  payload: {
    kind: 'professor_selection',
    resource: 'professors',
    selection_mode: 'replace',
    display: 'selected_only',
    archive_scope: 'active',
    matched_count: 2,
    excluded_count: 0,
    ui_effects: ['focus_window', 'navigate', 'replace_selection'],
  },
  selectedIds: [11, 12],
  ...overrides,
});

describe('validateAgentUiHandoff', () => {
  it('accepts a frozen professor selection', () => {
    const parsed = validateAgentUiHandoff(buildHandoff());

    expect(parsed.surface).toBe('professors.management');
    expect(parsed.selectedIds).toEqual([11, 12]);
    expect(parsed.payload).toEqual(
      expect.objectContaining({
        selection_mode: 'replace',
        display: 'selected_only',
      }),
    );
  });

  it.each([
    { selectedIds: [11, 11], reason: '重复' },
    { selectedIds: [0, 12], reason: '无效' },
    { route: '/', reason: '目标页面' },
    { schemaVersion: 2, reason: '当前版本' },
    { handoffId: '../invalid', reason: '交接 ID' },
    { expiresAt: 'not-a-date', reason: '过期时间' },
    { claimExpiresAt: 'not-a-date', reason: '租约时间' },
  ])('rejects unsafe professor handoffs: $reason', (override) => {
    expect(() => validateAgentUiHandoff(buildHandoff(override))).toThrow();
  });

  it('requires a home identity and the exact home route', () => {
    const withoutIdentity = buildHandoff({
      surface: 'professors.home',
      route: '/',
    });
    expect(() => validateAgentUiHandoff(withoutIdentity)).toThrow(/身份 ID/);

    const parsed = validateAgentUiHandoff({
      ...withoutIdentity,
      payload: { ...withoutIdentity.payload, identity_id: 8 },
    });
    if (parsed.surface !== 'professors.home') {
      throw new Error('expected professors.home handoff');
    }
    expect(parsed.payload.identity_id).toBe(8);
  });

  it('validates communication thread identity, professor and route together', () => {
    const handoff = buildHandoff({
      surface: 'communications.thread',
      route: '/workspace/21',
      selectionCount: 1,
      selectedIds: [],
      payload: {
        kind: 'communication_thread_context',
        resource: 'communications.threads',
        thread_id: '7:21',
        identity_id: 7,
        professor_id: 21,
        ui_effects: ['focus_window', 'navigate', 'focus_resource'],
      },
    });
    expect(validateAgentUiHandoff(handoff).surface).toBe(
      'communications.thread',
    );
    expect(() =>
      validateAgentUiHandoff({
        ...handoff,
        payload: { ...handoff.payload, thread_id: '8:21' },
      }),
    ).toThrow(/通信线程/);
  });
});
