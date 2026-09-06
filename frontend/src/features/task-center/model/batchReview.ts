export type BatchReviewItemActionType =
  | "template"
  | "regenerate"
  | "delete"
  | "submit";
export type BatchReviewItemActions = Record<number, BatchReviewItemActionType>;
