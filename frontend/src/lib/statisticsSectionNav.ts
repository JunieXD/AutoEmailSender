export const resolveStatisticsSectionNavTop = ({
  headerBottom = 0,
  summaryCardBottom,
  rootFontSize,
}: {
  headerBottom?: number;
  summaryCardBottom: number;
  rootFontSize: number;
}) =>
  Math.max(
    rootFontSize * 10,
    Math.ceil(headerBottom + rootFontSize * 1.5),
    Math.ceil(summaryCardBottom + rootFontSize * 3.5),
  );
