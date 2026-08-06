import { getCommunityMentorCatalog } from '@/entities/community-mentor/api/communityMentors';
import type { CommunityCatalogDTO } from '../model/types';

let catalogSessionCache: CommunityCatalogDTO | null = null;
let catalogCacheRequest: Promise<CommunityCatalogDTO> | null = null;
let catalogRefreshRequest: Promise<CommunityCatalogDTO> | null = null;

export const getCommunityMentorCatalogSessionSnapshot = () => catalogSessionCache;

export const requestCommunityMentorCatalog = (refresh: boolean) => {
  const requestInProgress = refresh ? catalogRefreshRequest : catalogCacheRequest;
  if (requestInProgress) {
    return requestInProgress;
  }

  const request = getCommunityMentorCatalog(refresh)
    .then((nextCatalog) => {
      catalogSessionCache = nextCatalog;
      return nextCatalog;
    })
    .finally(() => {
      if (refresh) {
        catalogRefreshRequest = null;
      } else {
        catalogCacheRequest = null;
      }
    });

  if (refresh) {
    catalogRefreshRequest = request;
  } else {
    catalogCacheRequest = request;
  }
  return request;
};

export const resetCommunityMentorCatalogSessionCacheForTests = () => {
  catalogSessionCache = null;
  catalogCacheRequest = null;
  catalogRefreshRequest = null;
};
