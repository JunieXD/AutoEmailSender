import { describe, expect, it } from 'vitest';

import {
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS as entityStatusLabels,
} from '@/entities/professor/model/types';
import {
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS as aggregateStatusLabels,
} from '@/types';

describe('entity type barrel', () => {
  it('keeps the aggregate value bound to the entity model', () => {
    expect(aggregateStatusLabels).toBe(entityStatusLabels);
  });
});
