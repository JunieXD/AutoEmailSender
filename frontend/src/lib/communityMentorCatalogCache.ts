import { getCommunityMentorCatalog } from '@/lib/api/communityMentorsApi';
import type { CommunityCatalogDTO } from '@/types';


const CATALOG_AUTO_REFRESH_INTERVAL_MS = 15 * 60 * 1000;

let catalogSessionCache: CommunityCatalogDTO | null = null;
let catalogCacheRequest: Promise<CommunityCatalogDTO> | null = null;
let catalogRefreshRequest: Promise<CommunityCatalogDTO> | null = null;
let catalogLastRefreshAttemptAt = 0;

export const getCommunityMentorCatalogSessionSnapshot = () => catalogSessionCache;

export const shouldAutomaticallyRefreshCommunityMentorCatalog = (
  catalog: CommunityCatalogDTO,
) =>
  catalog.source !== 'network' &&
  Date.now() - catalogLastRefreshAttemptAt >= CATALOG_AUTO_REFRESH_INTERVAL_MS;

export const requestCommunityMentorCatalog = (refresh: boolean) => {
  const requestInProgress = refresh ? catalogRefreshRequest : catalogCacheRequest;
  if (requestInProgress) {
    return requestInProgress;
  }

  if (refresh) {
    catalogLastRefreshAttemptAt = Date.now();
  }
  const request = getCommunityMentorCatalog(refresh)
    .then((nextCatalog) => {
      catalogSessionCache = nextCatalog;
      if (nextCatalog.source === 'network') {
        catalogLastRefreshAttemptAt = Date.now();
      }
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
  catalogLastRefreshAttemptAt = 0;
};
