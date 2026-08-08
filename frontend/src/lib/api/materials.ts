import { apiFetch, apiFetchBlob } from '@/lib/api/client';
import type { IdentityMaterialDTO, IdentityMaterialType } from '@/types';

export const uploadIdentityMaterial = (
  identityId: number,
  payload: {
    file: File;
    materialType: IdentityMaterialType;
    displayName?: string;
  },
) => {
  const formData = new FormData();
  formData.append('file', payload.file);
  formData.append('material_type', payload.materialType);
  if (payload.displayName?.trim()) {
    formData.append('display_name', payload.displayName.trim());
  }
  return apiFetch<IdentityMaterialDTO>(`/api/identities/${identityId}/materials`, {
    method: 'POST',
    body: formData,
  });
};

export const setPrimaryMaterial = (materialId: number) =>
  apiFetch<IdentityMaterialDTO>(`/api/materials/${materialId}/set-primary`, {
    method: 'POST',
  });

export const deleteMaterial = (materialId: number) =>
  apiFetch<void>(`/api/materials/${materialId}`, {
    method: 'DELETE',
  });

export const downloadMaterial = (materialId: number) =>
  apiFetchBlob(`/api/materials/${materialId}/download`);
