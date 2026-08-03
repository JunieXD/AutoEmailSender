export const MAX_SELECTED_COMMUNITY_MENTORS = 500;
export const MAX_LOADED_COMMUNITY_MENTORS = 2_000;

export const getVisibleRecordSelectionState = (
  selectedRecordIds: string[],
  visibleRecordIds: string[],
) => {
  const selectedRecordIdSet = new Set(selectedRecordIds);
  const selectedVisibleCount = visibleRecordIds.filter((id) =>
    selectedRecordIdSet.has(id),
  ).length;
  const allVisibleSelected =
    visibleRecordIds.length > 0 &&
    selectedVisibleCount === visibleRecordIds.length;
  return {
    selectedVisibleCount,
    allVisibleSelected,
    partiallyVisibleSelected:
      selectedVisibleCount > 0 && !allVisibleSelected,
  };
};

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
