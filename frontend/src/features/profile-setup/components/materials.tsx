import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import {
  canUseAsPrimaryMaterial,
  type MaterialFilterValue,
} from "@/features/profile-setup/model/profileForms";
import { formatApiDateTime } from "@/lib/dateTime";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import {
  MATERIAL_TYPE_LABELS,
  type IdentityDTO,
  type IdentityMaterialDTO,
  type IdentityMaterialType,
} from "@/types";
import clsx from "clsx";
import {
  Download,
  ExternalLink,
  FolderOpen,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import { getMaterialTypeLabel } from "../model/materialLabels";

const formatFileSize = (sizeBytes: number) => {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
};

const MATERIAL_TYPE_OPTIONS = Object.entries(MATERIAL_TYPE_LABELS) as [
  IdentityMaterialType,
  string,
][];

export const MaterialTypePicker = ({
  value,
  onChange,
}: {
  value: IdentityMaterialType;
  onChange: (value: IdentityMaterialType) => void;
}) => (
  <NativeSelectField
    value={value}
    onChange={(event) => onChange(event.target.value as IdentityMaterialType)}
    wrapperClassName="w-full max-w-xs"
    shellClassName="min-h-10 rounded-2xl border-stone-200 bg-white/92 px-4 py-2.5 shadow-sm shadow-stone-100/70"
  >
    {MATERIAL_TYPE_OPTIONS.map(([type, label]) => (
      <option key={type} value={type}>
        {label}
      </option>
    ))}
  </NativeSelectField>
);

export const MaterialFilterBar = ({
  value,
  materials,
  onChange,
}: {
  value: MaterialFilterValue;
  materials: IdentityMaterialDTO[];
  onChange: (value: MaterialFilterValue) => void;
}) => (
  <div className="flex flex-wrap gap-2">
    <button
      type="button"
      onClick={() => onChange("all")}
      className={clsx(
        "rounded-full border px-3 py-1.5 text-xs font-medium transition",
        value === "all"
          ? "border-stone-900 bg-stone-900 text-white shadow-sm shadow-stone-900/20"
          : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
      )}
    >
      全部 {materials.length}
    </button>
    {MATERIAL_TYPE_OPTIONS.map(([type, label]) => {
      const count = materials.filter(
        (material) => material.material_type === type,
      ).length;
      if (!count) {
        return null;
      }
      return (
        <button
          key={type}
          type="button"
          onClick={() => onChange(type)}
          className={clsx(
            "rounded-full border px-3 py-1.5 text-xs font-medium transition",
            value === type
              ? "border-primary bg-primary text-white shadow-sm shadow-primary/20"
              : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
          )}
        >
          {label} {count}
        </button>
      );
    })}
  </div>
);

export const MaterialSummaryCard = ({
  identity,
  onOpen,
}: {
  identity: IdentityDTO;
  onOpen: () => void;
}) => {
  const primaryMaterial = identity.current_primary_material;

  return (
    <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff8ef_55%,#fff3e1)] p-5 shadow-sm shadow-stone-200/70">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-stone-900">全局材料库</div>
            <div className="mt-1 text-xs text-stone-500">
              所有发件身份共享，共 {identity.materials.length} 份
              {primaryMaterial
                ? ` · 默认材料：${primaryMaterial.display_name}`
                : " · 当前未设默认材料"}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {MATERIAL_TYPE_OPTIONS.map(([type, label]) => {
              const count = identity.materials.filter(
                (material) => material.material_type === type,
              ).length;
              if (!count) {
                return null;
              }
              return (
                <span
                  key={type}
                  className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600"
                >
                  {label} {count}
                </span>
              );
            })}
          </div>
        </div>

        <button
          type="button"
          onClick={onOpen}
          className="inline-flex items-center gap-2 rounded-2xl border border-stone-300 bg-white/95 px-4 py-2.5 text-sm font-medium text-stone-800 shadow-sm transition hover:border-stone-400 hover:bg-white"
        >
          <FolderOpen className="h-4 w-4" />
          打开材料库
        </button>
      </div>
    </div>
  );
};

export const MaterialLibraryModal = ({
  open,
  identity,
  materials,
  busy,
  uploading,
  selectedMaterialType,
  materialFilter,
  highlightedMaterialId,
  onChangeMaterialType,
  onChangeMaterialFilter,
  onUpload,
  onOpen,
  onDownload,
  onClose,
  onSetPrimary,
  onDelete,
}: {
  open: boolean;
  identity: IdentityDTO;
  materials: IdentityMaterialDTO[];
  busy: boolean;
  uploading: boolean;
  selectedMaterialType: IdentityMaterialType;
  materialFilter: MaterialFilterValue;
  highlightedMaterialId: number | null;
  onChangeMaterialType: (value: IdentityMaterialType) => void;
  onChangeMaterialFilter: (value: MaterialFilterValue) => void;
  onUpload: (file: File) => void;
  onOpen: (material: IdentityMaterialDTO) => void;
  onDownload: (material: IdentityMaterialDTO) => void;
  onClose: () => void;
  onSetPrimary: (material: IdentityMaterialDTO) => void;
  onDelete: (material: IdentityMaterialDTO) => void;
}) => {
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);

  if (!open) {
    return null;
  }

  const primaryMaterial = identity.current_primary_material;
  const visibleMaterials =
    materialFilter === "all"
      ? materials
      : materials.filter(
          (material) => material.material_type === materialFilter,
        );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-stone-950/35 p-4 backdrop-blur-md sm:items-center"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="relative flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,#fffdfa,#fff7ee_18%,#ffffff_40%)] shadow-[0_30px_90px_-28px_rgba(41,37,36,0.45)]"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="border-b border-stone-200/80 bg-white/75 px-6 py-5 backdrop-blur-md">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.26em] text-stone-400">
                Global Material Library
              </div>
              <h3 className="mt-2 text-2xl font-semibold text-stone-900">
                全局材料管理
              </h3>
              <p className="mt-1 text-sm text-stone-500">
                {materials.length} 份共享材料
                {primaryMaterial
                  ? ` · 默认材料：${primaryMaterial.display_name}`
                  : " · 当前未设默认材料"}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
              aria-label="关闭材料库"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="border-b border-stone-200/80 bg-[#fffaf3] px-6 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-center">
              <div className="min-w-[6.5rem]">
                <div className="text-sm font-medium text-stone-900">
                  上传新材料
                </div>
                <div className="mt-1 text-xs text-stone-500">
                  上传一次，可供所有发件身份复用
                </div>
              </div>
              <MaterialTypePicker
                value={selectedMaterialType}
                onChange={onChangeMaterialType}
              />
              <span className="inline-flex items-center rounded-full border border-stone-200 bg-white/90 px-3 py-1.5 text-xs text-stone-600 shadow-sm shadow-stone-100/70">
                当前：{getMaterialTypeLabel(selectedMaterialType)}
              </span>
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-primary/20 bg-primary px-4 py-3 text-sm font-medium text-white shadow-sm shadow-primary/20 transition hover:bg-primary-dark">
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              上传材料
              <input
                type="file"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.currentTarget.value = "";
                  if (!file) {
                    return;
                  }
                  onUpload(file);
                }}
              />
            </label>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-stone-900">查看材料</div>
            </div>
            <MaterialFilterBar
              value={materialFilter}
              materials={materials}
              onChange={onChangeMaterialFilter}
            />
          </div>

          {materials.length === 0 ? (
            <div className="rounded-[28px] border border-dashed border-stone-200 bg-white/75 px-6 py-12 text-center text-sm text-stone-500">
              暂无材料。上传一份即可。
            </div>
          ) : visibleMaterials.length === 0 ? (
            <div className="rounded-[28px] border border-dashed border-stone-200 bg-white/75 px-6 py-12 text-center text-sm text-stone-500">
              当前筛选下还没有材料，试试切回“全部”。
            </div>
          ) : (
            <div className="space-y-3">
              {visibleMaterials.map((material) => {
                const canPromote = canUseAsPrimaryMaterial(material);
                return (
                  <article
                    key={material.id}
                    data-material-id={material.id}
                    className={clsx(
                      "rounded-[26px] border px-5 py-4 shadow-sm transition",
                      material.is_primary
                        ? "border-primary/20 bg-primary/5 shadow-primary/5"
                        : "border-stone-200 bg-white shadow-stone-100/60",
                      highlightedMaterialId === material.id &&
                        "border-amber-300 bg-amber-50/70 shadow-amber-100",
                    )}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate text-sm font-semibold text-stone-900">
                            {material.display_name}
                          </h3>
                          {material.is_primary ? (
                            <span className="rounded-full bg-primary px-2.5 py-1 text-[11px] font-medium text-white">
                              默认材料
                            </span>
                          ) : null}
                          {!canPromote ? (
                            <span className="rounded-full border border-stone-200 bg-stone-100 px-2.5 py-1 text-[11px] text-stone-500">
                              仅随信发送
                            </span>
                          ) : null}
                          <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-[11px] text-stone-600">
                            {MATERIAL_TYPE_LABELS[material.material_type]}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-stone-500">
                          <span>{material.original_filename}</span>
                          <span>{formatFileSize(material.size_bytes)}</span>
                          <span>{formatApiDateTime(material.created_at)}</span>
                          {(material.default_for_identity_ids?.length ?? 0) >
                          0 ? (
                            <span>
                              {material.default_for_identity_ids?.length}{" "}
                              个身份正在使用默认
                            </span>
                          ) : null}
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => onOpen(material)}
                          className="ui-btn-secondary"
                        >
                          <ExternalLink className="h-4 w-4" />
                          打开
                        </button>
                        <button
                          type="button"
                          onClick={() => onDownload(material)}
                          className="ui-btn-secondary"
                        >
                          <Download className="h-4 w-4" />
                          下载
                        </button>
                        {material.is_primary ? (
                          <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
                            已设为默认材料
                          </span>
                        ) : (
                          <button
                            type="button"
                            disabled={busy || !canPromote}
                            onClick={() => onSetPrimary(material)}
                            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {canPromote ? "设为默认材料" : "不可设默认材料"}
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => onDelete(material)}
                          className="ui-btn-danger disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
