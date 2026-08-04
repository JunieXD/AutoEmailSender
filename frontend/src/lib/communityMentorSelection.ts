export const MAX_LOADED_COMMUNITY_MENTORS = 2_000;
export const MAX_SELECTED_COMMUNITY_MENTORS = MAX_LOADED_COMMUNITY_MENTORS;
export const MAX_SELECTED_COMMUNITY_UNITS = 20;

export type CommunityUnitSelectionCandidate = {
  id: string;
  recordCount: number;
};

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

export const addFilteredCommunityUnitSelection = (
  currentUnitIds: string[],
  allUnits: CommunityUnitSelectionCandidate[],
  filteredUnits: CommunityUnitSelectionCandidate[],
) => {
  const recordCountById = new Map(
    allUnits.map((unit) => [unit.id, Math.max(0, unit.recordCount)]),
  );
  const nextUnitIds = [...currentUnitIds];
  const selectedUnitIds = new Set(currentUnitIds);
  let selectedRecordCount = currentUnitIds.reduce(
    (total, id) => total + (recordCountById.get(id) ?? 0),
    0,
  );
  let omittedByUnitLimit = 0;
  let omittedByRecordLimit = 0;

  filteredUnits.forEach((unit) => {
    if (selectedUnitIds.has(unit.id)) {
      return;
    }
    if (nextUnitIds.length >= MAX_SELECTED_COMMUNITY_UNITS) {
      omittedByUnitLimit += 1;
      return;
    }
    const recordCount = Math.max(0, unit.recordCount);
    if (selectedRecordCount + recordCount > MAX_LOADED_COMMUNITY_MENTORS) {
      omittedByRecordLimit += 1;
      return;
    }
    nextUnitIds.push(unit.id);
    selectedUnitIds.add(unit.id);
    selectedRecordCount += recordCount;
  });

  return {
    unitIds: nextUnitIds,
    selectedRecordCount,
    omittedByUnitLimit,
    omittedByRecordLimit,
  };
};
