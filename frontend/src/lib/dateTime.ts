const HAS_TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:\d{2})$/i;
const LEGACY_SPACE_DATETIME_PATTERN = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/;

export const parseApiDateTime = (value: string) => {
  const trimmed = value.trim();
  const normalized = LEGACY_SPACE_DATETIME_PATTERN.test(trimmed)
    ? trimmed.replace(' ', 'T')
    : trimmed;
  const withTimezone = HAS_TIMEZONE_SUFFIX.test(normalized)
    ? normalized
    : `${normalized}Z`;
  return new Date(withTimezone);
};

export const formatApiDateTime = (
  value: string,
  options?: Intl.DateTimeFormatOptions,
) =>
  parseApiDateTime(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...options,
  });