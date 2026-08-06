import { describe, expect, it } from 'vitest';

import * as communityApi from '@/entities/community-mentor/api/communityMentors';
import * as legacyCommunityApi from '@/lib/api/communityMentorsApi';
import * as enrichmentApi from '@/entities/professor/api/informationEnrichment';
import * as legacyEnrichmentApi from '@/lib/api/professorInformationEnrichmentApi';
import * as professorApi from '@/entities/professor/api/professors';
import * as legacyProfessorApi from '@/lib/api/professorsApi';
import {
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS as entityStatusLabels,
} from '@/entities/professor/model/types';
import {
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS as legacyStatusLabels,
} from '@/types';

describe('professor and community entity compatibility exports', () => {
  it('keeps the legacy professor API bound to the entity implementation', () => {
    expect(legacyProfessorApi.listProfessors).toBe(professorApi.listProfessors);
    expect(legacyProfessorApi.updateProfessor).toBe(professorApi.updateProfessor);
  });

  it('keeps the legacy enrichment API bound to the entity implementation', () => {
    expect(legacyEnrichmentApi.createProfessorInformationEnrichmentJob).toBe(
      enrichmentApi.createProfessorInformationEnrichmentJob,
    );
    expect(legacyEnrichmentApi.restoreProfessorInformationEnrichmentJob).toBe(
      enrichmentApi.restoreProfessorInformationEnrichmentJob,
    );
  });

  it('keeps the legacy community API bound to the entity implementation', () => {
    expect(legacyCommunityApi.getCommunityMentorCatalog).toBe(
      communityApi.getCommunityMentorCatalog,
    );
    expect(legacyCommunityApi.importCommunityMentors).toBe(
      communityApi.importCommunityMentors,
    );
  });

  it('keeps the legacy type barrel value bound to the entity model', () => {
    expect(legacyStatusLabels).toBe(entityStatusLabels);
  });
});
