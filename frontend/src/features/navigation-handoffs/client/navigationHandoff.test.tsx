import { beforeEach, describe, expect, it } from 'vitest';
import {
  LEGACY_BATCH_RESEND_CONTEXT_KEY,
  LEGACY_SELECTED_PROFESSOR_IDS_KEY,
  NAVIGATION_HANDOFF_STORAGE_KEY,
  clearCreateTaskNavigationHandoff,
  clearCreateTaskResendContext,
  readCreateTaskNavigationHandoff,
  writeCreateTaskNavigationHandoff,
} from './navigationHandoff';

const resendContext = {
  sourceTaskId: 9,
  sourceTaskName: '旧任务',
  identityId: 3,
  professorIds: [11, 12],
  requiresRegeneration: false,
  defaults: {
    identity_id: 3,
    outreach_generation_mode: 'llm' as const,
    outreach_template_id: null,
    outreach_template_name_snapshot: null,
    outreach_template_subject: '主题',
    outreach_template_body_text: '正文',
    outreach_template_body_html: '<p>正文</p>',
    primary_material_id: null,
    selected_material_ids: [],
  },
  warnings: [],
};

describe('create-task navigation handoff', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('stores selection and resend context atomically across reload reads', () => {
    writeCreateTaskNavigationHandoff([11, 12], resendContext);

    expect(readCreateTaskNavigationHandoff()).toEqual(
      expect.objectContaining({
        kind: 'create_batch_task',
        professorIds: [11, 12],
        resendContext: expect.objectContaining({ sourceTaskId: 9 }),
      }),
    );
    expect(window.sessionStorage.getItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(LEGACY_BATCH_RESEND_CONTEXT_KEY)).toBeNull();
  });

  it('migrates a fresh legacy handoff over a stale v1 record', () => {
    writeCreateTaskNavigationHandoff([99]);
    window.sessionStorage.setItem(
      LEGACY_SELECTED_PROFESSOR_IDS_KEY,
      JSON.stringify([11, 12]),
    );
    window.sessionStorage.setItem(
      LEGACY_BATCH_RESEND_CONTEXT_KEY,
      JSON.stringify(resendContext),
    );

    const migrated = readCreateTaskNavigationHandoff();

    expect(migrated?.professorIds).toEqual([11, 12]);
    expect(migrated?.resendContext?.sourceTaskId).toBe(9);
    expect(window.sessionStorage.getItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY)).toBeNull();
  });

  it('defaults legacy resend context without regeneration metadata', () => {
    const legacyContext = { ...resendContext } as Partial<typeof resendContext>;
    delete legacyContext.requiresRegeneration;
    window.sessionStorage.setItem(
      LEGACY_SELECTED_PROFESSOR_IDS_KEY,
      JSON.stringify([11, 12]),
    );
    window.sessionStorage.setItem(
      LEGACY_BATCH_RESEND_CONTEXT_KEY,
      JSON.stringify(legacyContext),
    );

    expect(readCreateTaskNavigationHandoff()?.resendContext).toEqual({
      ...resendContext,
      requiresRegeneration: true,
    });
  });

  it('clears all navigation keys when legacy resend context is corrupt', () => {
    window.sessionStorage.setItem(
      LEGACY_SELECTED_PROFESSOR_IDS_KEY,
      JSON.stringify([11, 12]),
    );
    window.sessionStorage.setItem(LEGACY_BATCH_RESEND_CONTEXT_KEY, '{');

    expect(readCreateTaskNavigationHandoff()).toBeNull();
    expect(window.sessionStorage.getItem(NAVIGATION_HANDOFF_STORAGE_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(LEGACY_BATCH_RESEND_CONTEXT_KEY)).toBeNull();
  });

  it('clears corrupt, mismatched and expired records', () => {
    window.sessionStorage.setItem(
      NAVIGATION_HANDOFF_STORAGE_KEY,
      JSON.stringify({
        schemaVersion: 1,
        kind: 'create_batch_task',
        target: '/create-task',
        createdAt: Date.now() - 10_000,
        expiresAt: Date.now() - 1,
        professorIds: [11],
        resendContext: null,
      }),
    );
    expect(readCreateTaskNavigationHandoff()).toBeNull();
    expect(window.sessionStorage.getItem(NAVIGATION_HANDOFF_STORAGE_KEY)).toBeNull();

    expect(() =>
      writeCreateTaskNavigationHandoff([11], resendContext),
    ).toThrow(/不匹配/);
    clearCreateTaskNavigationHandoff();
    expect(window.sessionStorage.length).toBe(0);
  });

  it('rejects future-dated and structurally corrupt resend records', () => {
    const future = Date.now() + 60 * 60 * 1_000;
    window.sessionStorage.setItem(
      NAVIGATION_HANDOFF_STORAGE_KEY,
      JSON.stringify({
        schemaVersion: 1,
        kind: 'create_batch_task',
        target: '/create-task',
        createdAt: future,
        expiresAt: future + 60 * 60 * 1_000,
        professorIds: [11],
        resendContext: null,
      }),
    );
    expect(readCreateTaskNavigationHandoff()).toBeNull();

    const now = Date.now();
    window.sessionStorage.setItem(
      NAVIGATION_HANDOFF_STORAGE_KEY,
      JSON.stringify({
        schemaVersion: 1,
        kind: 'create_batch_task',
        target: '/create-task',
        createdAt: now,
        expiresAt: now + 60 * 60 * 1_000,
        professorIds: [11, 12],
        resendContext: {
          ...resendContext,
          defaults: {
            ...resendContext.defaults,
            selected_material_ids: 'not-an-array',
          },
        },
      }),
    );
    expect(readCreateTaskNavigationHandoff()).toBeNull();
    expect(window.sessionStorage.getItem(NAVIGATION_HANDOFF_STORAGE_KEY)).toBeNull();
  });

  it('preserves large regular page selections beyond the Agent handoff limit', () => {
    const professorIds = Array.from({ length: 10_001 }, (_, index) => index + 1);

    writeCreateTaskNavigationHandoff(professorIds);

    expect(readCreateTaskNavigationHandoff()?.professorIds).toHaveLength(10_001);
  });

  it('clears only resend context while preserving the atomic professor selection', () => {
    writeCreateTaskNavigationHandoff([11, 12], resendContext);

    clearCreateTaskResendContext();

    expect(readCreateTaskNavigationHandoff()).toEqual(
      expect.objectContaining({
        professorIds: [11, 12],
        resendContext: null,
      }),
    );
  });
});
