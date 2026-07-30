import { useId } from "react";

type QqOutlineLogoProps = {
  className?: string;
};

// Penguin geometry from the vector logo embedded in the current QQ official site header.
// Only paths that contribute to the outer silhouette are retained.
const QQ_OUTER_SHAPE_PATH = [
  "M9.56 28.668c-1.981 0-3.8-.666-4.972-1.662-.594.179-1.355.466-1.835.823-.41.305-.359.615-.285.741.325.55 5.577.351 7.094.18v-.082z",
  "M9.56 28.668c1.98 0 3.8-.666 4.971-1.662.595.179 1.356.466 1.836.823.41.305.359.615.285.741-.326.55-5.578.351-7.094.18v-.082z",
  "M9.573 16.55c3.272-.023 5.894-.476 6.784-.72a.84.84 0 0 0 .325-.163c0-.03.013-.535.013-.796-.002-4.39-2.063-8.802-7.135-8.802-5.073 0-7.134 4.411-7.134 8.802 0 .261.013.767.013.796 0 0 .093.098.261.145.821.229 3.493.715 6.849.738zM18.465 20.391c-.204-.655-.48-1.42-.76-2.157 0 0-.188-.005-.285.016-2.33.503-5.536.869-7.847.841h-.024c-2.311.028-5.632-.356-7.847-.84-.098-.021-.285-.017-.285-.017-.28.735-.558 1.5-.76 2.157-.893 2.785-.71 4.331-.413 4.444.423.163 1.99-2.347 1.99-2.347 0 2.457 2.207 6.227 7.26 6.263h.135c5.053-.036 7.26-3.806 7.26-6.263 0 0 1.567 2.508 1.99 2.347.295-.113.478-1.659-.411-4.444z",
  "M9.549 19.23c-2.53 0-5.424-.294-8.132-.994l1.024-2.568s3.014.756 7.108.756h.024c4.093 0 7.108-.755 7.108-.755l1.024 2.567c-2.709.702-5.602.994-8.132.994z",
].join(" ");

export const QqOutlineLogo = ({ className }: QqOutlineLogoProps) => {
  const uniqueId = useId().replace(/:/g, "");
  const shapeId = `qq-outer-shape-${uniqueId}`;
  const outlineMaskId = `qq-outer-outline-mask-${uniqueId}`;

  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="-6 2 31 31"
      className={className}
      data-qq-logo="outline"
    >
      <defs>
        <path
          id={shapeId}
          d={QQ_OUTER_SHAPE_PATH}
          data-qq-outer-shape="official-vector"
        />
        <mask
          id={outlineMaskId}
          x="-6"
          y="2"
          width="31"
          height="31"
          maskUnits="userSpaceOnUse"
          maskContentUnits="userSpaceOnUse"
        >
          <use
            href={`#${shapeId}`}
            fill="white"
            stroke="white"
            strokeWidth="5"
            strokeLinecap="round"
            strokeLinejoin="round"
            data-qq-outline-stroke="rounded"
          />
          <use
            href={`#${shapeId}`}
            fill="black"
            stroke="black"
            strokeWidth="1"
            strokeLinecap="round"
            strokeLinejoin="round"
            data-qq-outline-cutout="seamless"
          />
        </mask>
      </defs>
      <rect
        x="-6"
        y="2"
        width="31"
        height="31"
        fill="currentColor"
        mask={`url(#${outlineMaskId})`}
      />
    </svg>
  );
};
