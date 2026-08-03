export const MAX_SELECTED_COMMUNITY_MENTORS = 500;

export const addVisibleRecordSelection = (
  currentRecordIds: string[],
  visibleRecordIds: string[],
) => {
  const currentRecordIdSet = new Set(currentRecordIds);
  const unselectedVisibleIds = visibleRecordIds.filter(
    (id) => !currentRecordIdSet.has(id),
  );
  const availableSlots = Math.max(
    0,
    MAX_SELECTED_COMMUNITY_MENTORS - currentRecordIds.length,
  );
  const addedIds = unselectedVisibleIds.slice(0, availableSlots);
  return {
    recordIds: [...currentRecordIds, ...addedIds],
    omittedCount: unselectedVisibleIds.length - addedIds.length,
  };
};
