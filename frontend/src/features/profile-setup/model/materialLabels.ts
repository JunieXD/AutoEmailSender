import { MATERIAL_TYPE_LABELS, type IdentityMaterialType } from "@/types";

export const getMaterialTypeLabel = (value: IdentityMaterialType) =>
  MATERIAL_TYPE_LABELS[value];
