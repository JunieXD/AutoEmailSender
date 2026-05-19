export const PAGE_SIZE = 10;

export const getTotalPages = (totalCount: number, pageSize = PAGE_SIZE) =>
  Math.max(1, Math.ceil(totalCount / pageSize));

export const getPageItems = <T,>(
  items: T[],
  page: number,
  pageSize = PAGE_SIZE,
) => {
  const startIndex = (page - 1) * pageSize;
  return items.slice(startIndex, startIndex + pageSize);
};
