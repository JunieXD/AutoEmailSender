from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "professor-import-contract.v1.json"
)


@dataclass(frozen=True, slots=True)
class ContractIssue:
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


class ContractValidationError(ValueError):
    def __init__(self, issues: list[ContractIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{item.path}: {item.message}" for item in issues))


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


CONTRACT = load_contract()
SAFE_COLUMNS = tuple(CONTRACT["safe_columns"])
FULL_COLUMNS = tuple(CONTRACT["full_columns"])
ALLOWED_TITLES = tuple(CONTRACT["allowed_titles"])
TITLE_PRIORITY = {title: index for index, title in enumerate(ALLOWED_TITLES)}
TITLE_ALIASES = {
    key.casefold(): value for key, value in CONTRACT["title_aliases"].items()
}
TITLE_EXCLUSION_MARKERS = tuple(
    marker.casefold() for marker in CONTRACT["title_exclusion_markers"]
)
MAX_LENGTHS = CONTRACT["max_lengths"]
RECENT_PAPERS_MAX_ITEMS = int(CONTRACT["recent_papers_max_items"])
REVIEW_REASONS = frozenset(CONTRACT["review_reasons"])

EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+$")
EMAIL_DOMAIN_LABEL_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
EMAIL_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "＠": "@",
        "．": ".",
        "。": ".",
        "﹒": ".",
        "｡": ".",
        "（": "(",
        "）": ")",
        "［": "[",
        "］": "]",
        "【": "[",
        "】": "]",
        "｛": "{",
        "｝": "}",
    }
)
EMAIL_INVISIBLE_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]")
EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN = re.compile(r"邮箱符号")
EMAIL_AT_PATTERN = re.compile(
    r"(?:[\(\[]\s*at\s*[\)\]]|(?:(?<=^)|(?<=\s))at(?=$|\s))",
    re.IGNORECASE,
)
EMAIL_DOT_PATTERN = re.compile(
    r"(?:[\(\[]\s*dot\s*[\)\]]|(?:(?<=^)|(?<=\s))dot(?=$|\s))",
    re.IGNORECASE,
)
EMAIL_CHINESE_DOT_PATTERN = re.compile(r"(?<=[A-Za-z0-9])\s*点\s*(?=[A-Za-z0-9])")
TITLE_SPLIT_PATTERN = re.compile(r"[、，,/／|｜；;\s]+")
RESEARCH_SPLIT_PATTERN = re.compile(r"[；;|｜\n]+")
RECENT_PAPERS_SPLIT_PATTERN = re.compile(r"[|；;\n]+")
TAG_SPLIT_PATTERN = re.compile(r"[；;|｜,，]+")
INVALID_XML_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PLACEHOLDER_VALUES = frozenset(
    {"none", "null", "n/a", "na", "unknown", "未知", "暂无", "无", "不详", "-"}
)
SPREADSHEET_ERROR_VALUES = frozenset(
    {
        "#NULL!",
        "#DIV/0!",
        "#VALUE!",
        "#REF!",
        "#NAME?",
        "#NUM!",
        "#N/A",
        "#GETTING_DATA",
        "#SPILL!",
        "#CALC!",
        "#FIELD!",
        "#BLOCKED!",
        "#UNKNOWN!",
    }
)
GENERIC_EMAIL_LOCAL_PARTS = frozenset(
    {
        "admin",
        "admission",
        "admissions",
        "contact",
        "faculty",
        "graduate",
        "help",
        "hr",
        "info",
        "office",
        "postgraduate",
        "secretary",
        "support",
        "webmaster",
    }
)
SOURCE_ROLES = frozenset({"listing", "profile", "other"})
SOURCE_STATUSES = frozenset({"used", "skipped", "failed"})
REVIEW_FIELDS = ("name", "email", "profile_url", "source_url", "reason", "details")
SOURCE_FIELDS = ("url", "role", "status", "note")


def clean_text(value: object, *, path: str, issues: list[ContractIssue]) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        issues.append(ContractIssue(path, "必须是字符串或空值"))
        return ""
    text = str(value).strip()
    if INVALID_XML_CONTROL_PATTERN.search(text):
        issues.append(ContractIssue(path, "包含 XLSX/XML 不允许的控制字符"))
    if len(text) > int(MAX_LENGTHS["xlsx_cell"]):
        issues.append(
            ContractIssue(
                path,
                f"超过 XLSX 单元格上限 {MAX_LENGTHS['xlsx_cell']} 字符",
            )
        )
    if text.casefold() in PLACEHOLDER_VALUES:
        issues.append(ContractIssue(path, "占位文本必须改为空字符串"))
    if text.upper() in SPREADSHEET_ERROR_VALUES:
        issues.append(ContractIssue(path, "不得把电子表格错误值作为字段内容"))
    return text


def normalize_professor_email(value: str | None) -> str:
    if value is None:
        return ""
    normalized = unescape(str(value)).strip().lower()
    normalized = normalized.translate(EMAIL_FULLWIDTH_TRANSLATION)
    normalized = EMAIL_INVISIBLE_PATTERN.sub("", normalized)
    normalized = EMAIL_CHINESE_EMAIL_SYMBOL_PATTERN.sub("@", normalized)
    normalized = EMAIL_AT_PATTERN.sub("@", normalized)
    normalized = EMAIL_DOT_PATTERN.sub(".", normalized)
    normalized = EMAIL_CHINESE_DOT_PATTERN.sub(".", normalized)
    normalized = re.sub(r"\s*@\s*", "@", normalized)
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    if normalized.count("@") == 1:
        local_part, domain = normalized.split("@", 1)
        domain = re.sub(r"\.{2,}", ".", domain).strip(".")
        if domain:
            normalized = f"{local_part}@{domain}"
    return normalized


def is_valid_professor_email(email: str) -> bool:
    cleaned = email.strip()
    if cleaned.count("@") != 1:
        return False
    local_part, domain = cleaned.split("@", 1)
    if not local_part or not domain or not EMAIL_LOCAL_PATTERN.fullmatch(local_part):
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if not all(EMAIL_DOMAIN_LABEL_PATTERN.fullmatch(label) for label in labels):
        return False
    return labels[-1].isalpha() and len(labels[-1]) >= 2


def is_generic_email(email: str) -> bool:
    local_part = email.split("@", 1)[0].casefold()
    return local_part in GENERIC_EMAIL_LOCAL_PARTS


def normalize_title(value: str) -> str:
    if not value:
        return ""
    folded = value.casefold()
    for marker in TITLE_EXCLUSION_MARKERS:
        if marker.isascii():
            if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", folded):
                return ""
        elif marker in folded:
            return ""
    matches: list[str] = []
    occupied: list[tuple[int, int]] = []

    # Match longer English aliases first so "Associate Professor" is not
    # accidentally reduced to the nested word "Professor". Real faculty
    # pages commonly append qualifications around the title, so requiring an
    # exact whole-cell match would be too brittle.
    for alias in sorted(TITLE_ALIASES, key=len, reverse=True):
        pattern = re.compile(rf"(?<![a-z]){re.escape(alias.rstrip('.'))}\.?(?![a-z])")
        for match in pattern.finditer(folded):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            matches.append(TITLE_ALIASES[alias])

    # Apply the same longest-first rule to Chinese titles because values such
    # as “副教授” and “特聘研究员” contain shorter supported titles.
    for title in sorted(ALLOWED_TITLES, key=len, reverse=True):
        for match in re.finditer(re.escape(title), value):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            matches.append(title)
    if not matches:
        return ""
    return min(set(matches), key=lambda item: TITLE_PRIORITY[item])


def normalize_list_value(
    value: object,
    *,
    path: str,
    split_pattern: re.Pattern[str],
    issues: list[ContractIssue],
    split_list_items: bool = True,
) -> list[str]:
    if value is None or value == "":
        return []
    raw_items: list[object]
    if isinstance(value, str):
        raw_items = split_pattern.split(value)
    elif isinstance(value, list):
        raw_items = []
        for raw_item in value:
            if split_list_items and isinstance(raw_item, str):
                raw_items.extend(split_pattern.split(raw_item))
            else:
                raw_items.append(raw_item)
    else:
        issues.append(ContractIssue(path, "必须是字符串、字符串数组或空值"))
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        item = clean_text(raw_item, path=f"{path}[{index}]", issues=issues)
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def normalize_url(
    value: object,
    *,
    path: str,
    required: bool,
    issues: list[ContractIssue],
) -> str:
    text = clean_text(value, path=path, issues=issues)
    if not text:
        if required:
            issues.append(ContractIssue(path, "必须提供确切的官方证据 URL"))
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        parsed = None
        port = None
    if (
        parsed is None
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port is not None
        and not 0 < port < 65536
    ):
        issues.append(ContractIssue(path, "必须是无凭据的绝对 http/https URL"))
        return text
    fragment = parsed.fragment
    if not (fragment.startswith("/") or fragment.startswith("!/")):
        parsed = parsed._replace(fragment="")
    normalized = urlunsplit(parsed).rstrip("/")
    field_name = path.rsplit(".", 1)[-1]
    length_key = "profile_url" if field_name == "profile_url" else "source_url"
    if len(normalized) > int(MAX_LENGTHS[length_key]):
        issues.append(
            ContractIssue(
                path,
                f"超过 {MAX_LENGTHS[length_key]} 字符",
            )
        )
    return normalized


def _validate_length(
    value: str,
    *,
    field: str,
    path: str,
    issues: list[ContractIssue],
) -> None:
    limit = MAX_LENGTHS.get(field)
    if limit is not None and len(value) > int(limit):
        issues.append(ContractIssue(path, f"超过 {limit} 字符"))


def canonicalize_record(
    raw: object,
    *,
    index: int,
    include_user_fields: bool,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    path = f"records[{index}]"
    issues: list[ContractIssue] = []
    normalizations: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ContractValidationError([ContractIssue(path, "必须是对象")])

    missing = [field for field in FULL_COLUMNS if field not in raw]
    extra = [str(field) for field in raw if field not in FULL_COLUMNS]
    if missing:
        issues.append(ContractIssue(path, f"缺少字段：{', '.join(missing)}"))
    if extra:
        issues.append(ContractIssue(path, f"包含未知字段：{', '.join(extra)}"))

    cleaned: dict[str, str] = {}
    for field in (
        "name",
        "email",
        "title",
        "university",
        "school",
        "department",
        "profile_url",
        "source_url",
        "personal_note",
    ):
        if field in {"profile_url", "source_url"}:
            continue
        cleaned[field] = clean_text(
            raw.get(field),
            path=f"{path}.{field}",
            issues=issues,
        )

    name = cleaned["name"]
    if not name:
        issues.append(ContractIssue(f"{path}.name", "不能为空"))
    elif name.startswith("#") or name.startswith("示例："):
        issues.append(ContractIssue(f"{path}.name", "会被系统当作说明或示例行跳过"))
    _validate_length(name, field="name", path=f"{path}.name", issues=issues)

    raw_email = cleaned["email"]
    email = normalize_professor_email(raw_email)
    if not email:
        issues.append(ContractIssue(f"{path}.email", "不能为空"))
    elif not is_valid_professor_email(email):
        issues.append(ContractIssue(f"{path}.email", "规范化后仍不是系统接受的邮箱"))
    elif is_generic_email(email):
        issues.append(
            ContractIssue(
                f"{path}.email",
                "疑似通用或共享邮箱，应移入 review 而不是直接导入",
            )
        )
    _validate_length(email, field="email", path=f"{path}.email", issues=issues)
    if raw_email and email != raw_email:
        normalizations.append({"path": f"{path}.email", "from": raw_email, "to": email})

    raw_title = cleaned["title"]
    title = normalize_title(raw_title)
    if raw_title and not title:
        issues.append(
            ContractIssue(
                f"{path}.title",
                "无法映射到系统职称白名单；请改为支持的中文职称或留空",
            )
        )
    elif raw_title and title != raw_title:
        normalizations.append({"path": f"{path}.title", "from": raw_title, "to": title})
    _validate_length(title, field="title", path=f"{path}.title", issues=issues)

    for field in ("university", "school", "department"):
        _validate_length(
            cleaned[field],
            field=field,
            path=f"{path}.{field}",
            issues=issues,
        )

    research_items = normalize_list_value(
        raw.get("research_direction"),
        path=f"{path}.research_direction",
        split_pattern=RESEARCH_SPLIT_PATTERN,
        issues=issues,
    )
    research_direction = CONTRACT["research_direction_separator"].join(research_items)

    recent_papers = normalize_list_value(
        raw.get("recent_papers"),
        path=f"{path}.recent_papers",
        split_pattern=RECENT_PAPERS_SPLIT_PATTERN,
        issues=issues,
        split_list_items=False,
    )
    if isinstance(raw.get("recent_papers"), list):
        for paper_index, paper in enumerate(recent_papers):
            if RECENT_PAPERS_SPLIT_PATTERN.search(paper):
                issues.append(
                    ContractIssue(
                        f"{path}.recent_papers[{paper_index}]",
                        "单篇标题包含系统论文分隔符 |、中英文分号或换行，无法无损导入；请移入复核或省略该篇",
                    )
                )
    if len(recent_papers) > RECENT_PAPERS_MAX_ITEMS:
        issues.append(
            ContractIssue(
                f"{path}.recent_papers",
                f"最多 {RECENT_PAPERS_MAX_ITEMS} 篇；请先明确选择，不要依赖系统静默截断",
            )
        )
    recent_papers_text = CONTRACT["recent_papers_separator"].join(recent_papers)

    profile_url = normalize_url(
        raw.get("profile_url"),
        path=f"{path}.profile_url",
        required=False,
        issues=issues,
    )
    source_url = normalize_url(
        raw.get("source_url"),
        path=f"{path}.source_url",
        required=True,
        issues=issues,
    )

    tags = normalize_list_value(
        raw.get("tags"),
        path=f"{path}.tags",
        split_pattern=TAG_SPLIT_PATTERN,
        issues=issues,
    )
    for tag_index, tag in enumerate(tags):
        _validate_length(
            tag,
            field="tag",
            path=f"{path}.tags[{tag_index}]",
            issues=issues,
        )
    tags_text = CONTRACT["tags_separator"].join(tags)

    personal_note = cleaned["personal_note"]
    _validate_length(
        personal_note,
        field="personal_note",
        path=f"{path}.personal_note",
        issues=issues,
    )
    if not include_user_fields and (tags_text or personal_note):
        issues.append(
            ContractIssue(
                path,
                "默认安全模式不写 tags/personal_note；清空它们或显式使用 --include-user-fields",
            )
        )

    if issues:
        raise ContractValidationError(issues)

    record = {
        "name": name,
        "email": email,
        "title": title,
        "university": cleaned["university"],
        "school": cleaned["school"],
        "department": cleaned["department"],
        "research_direction": research_direction,
        "recent_papers": recent_papers_text,
        "profile_url": profile_url,
        "source_url": source_url,
        "tags": tags_text,
        "personal_note": personal_note,
    }
    return record, normalizations


def canonicalize_review(raw: object, *, index: int) -> dict[str, str]:
    path = f"review[{index}]"
    issues: list[ContractIssue] = []
    if not isinstance(raw, dict):
        raise ContractValidationError([ContractIssue(path, "必须是对象")])
    missing = [field for field in REVIEW_FIELDS if field not in raw]
    extra = [str(field) for field in raw if field not in REVIEW_FIELDS]
    if missing:
        issues.append(ContractIssue(path, f"缺少字段：{', '.join(missing)}"))
    if extra:
        issues.append(ContractIssue(path, f"包含未知字段：{', '.join(extra)}"))
    item = {
        field: clean_text(raw.get(field), path=f"{path}.{field}", issues=issues)
        for field in REVIEW_FIELDS
    }
    if item["email"]:
        item["email"] = normalize_professor_email(item["email"])
    for field in ("profile_url", "source_url"):
        item[field] = normalize_url(
            item[field],
            path=f"{path}.{field}",
            required=False,
            issues=issues,
        )
    if item["reason"] not in REVIEW_REASONS:
        issues.append(
            ContractIssue(
                f"{path}.reason",
                f"必须是：{', '.join(sorted(REVIEW_REASONS))}",
            )
        )
    if not item["name"] and not item["profile_url"] and not item["source_url"]:
        issues.append(ContractIssue(path, "至少提供姓名、个人主页或来源链接之一"))
    if not item["details"]:
        issues.append(ContractIssue(f"{path}.details", "必须简要说明复核原因"))
    if issues:
        raise ContractValidationError(issues)
    return item


def canonicalize_source(raw: object, *, index: int) -> dict[str, str]:
    path = f"sources[{index}]"
    issues: list[ContractIssue] = []
    if not isinstance(raw, dict):
        raise ContractValidationError([ContractIssue(path, "必须是对象")])
    missing = [field for field in SOURCE_FIELDS if field not in raw]
    extra = [str(field) for field in raw if field not in SOURCE_FIELDS]
    if missing:
        issues.append(ContractIssue(path, f"缺少字段：{', '.join(missing)}"))
    if extra:
        issues.append(ContractIssue(path, f"包含未知字段：{', '.join(extra)}"))
    item = {
        field: clean_text(raw.get(field), path=f"{path}.{field}", issues=issues)
        for field in SOURCE_FIELDS
    }
    item["url"] = normalize_url(
        item["url"],
        path=f"{path}.url",
        required=True,
        issues=issues,
    )
    if item["role"] not in SOURCE_ROLES:
        issues.append(
            ContractIssue(f"{path}.role", f"必须是：{', '.join(sorted(SOURCE_ROLES))}")
        )
    if item["status"] not in SOURCE_STATUSES:
        issues.append(
            ContractIssue(
                f"{path}.status",
                f"必须是：{', '.join(sorted(SOURCE_STATUSES))}",
            )
        )
    if issues:
        raise ContractValidationError(issues)
    return item


def canonicalize_payload(
    raw: object,
    *,
    include_user_fields: bool,
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    if not isinstance(raw, dict):
        raise ContractValidationError([ContractIssue("$", "顶层必须是对象")])
    issues: list[ContractIssue] = []
    expected = {"records", "review", "sources"}
    missing = sorted(expected.difference(raw))
    extra = sorted(str(key) for key in set(raw).difference(expected))
    if missing:
        issues.append(ContractIssue("$", f"缺少顶层字段：{', '.join(missing)}"))
    if extra:
        issues.append(ContractIssue("$", f"包含未知顶层字段：{', '.join(extra)}"))
    for key in expected:
        if key in raw and not isinstance(raw[key], list):
            issues.append(ContractIssue(key, "必须是数组"))
    if issues:
        raise ContractValidationError(issues)

    records: list[dict[str, str]] = []
    review: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    normalizations: list[dict[str, str]] = []
    collected_issues: list[ContractIssue] = []

    for index, item in enumerate(raw["records"]):
        try:
            record, changes = canonicalize_record(
                item,
                index=index,
                include_user_fields=include_user_fields,
            )
            records.append(record)
            normalizations.extend(changes)
        except ContractValidationError as error:
            collected_issues.extend(error.issues)

    for index, item in enumerate(raw["review"]):
        try:
            review.append(canonicalize_review(item, index=index))
        except ContractValidationError as error:
            collected_issues.extend(error.issues)

    for index, item in enumerate(raw["sources"]):
        try:
            sources.append(canonicalize_source(item, index=index))
        except ContractValidationError as error:
            collected_issues.extend(error.issues)

    seen_emails: dict[str, int] = {}
    for index, record in enumerate(records):
        email = record["email"]
        if email in seen_emails:
            collected_issues.append(
                ContractIssue(
                    f"records[{index}].email",
                    f"与 records[{seen_emails[email]}] 重复；系统会静默覆盖，必须先合并或复核",
                )
            )
        else:
            seen_emails[email] = index

    source_urls: set[str] = set()
    for index, item in enumerate(sources):
        if item["url"] in source_urls:
            collected_issues.append(
                ContractIssue(
                    f"sources[{index}].url",
                    "来源 URL 重复；请合并状态和说明",
                )
            )
        source_urls.add(item["url"])
    if not source_urls:
        collected_issues.append(
            ContractIssue("sources", "至少记录一个实际访问、跳过或失败的入口页面")
        )
    for index, record in enumerate(records):
        if record["source_url"] not in source_urls:
            collected_issues.append(
                ContractIssue(
                    f"records[{index}].source_url",
                    "没有对应的 sources 记录",
                )
            )
        if record["profile_url"] and record["profile_url"] not in source_urls:
            collected_issues.append(
                ContractIssue(
                    f"records[{index}].profile_url",
                    "个人主页没有对应的 sources 记录",
                )
            )
    for index, item in enumerate(review):
        for field in ("profile_url", "source_url"):
            if item[field] and item[field] not in source_urls:
                collected_issues.append(
                    ContractIssue(
                        f"review[{index}].{field}",
                        "复核页面没有对应的 sources 记录",
                    )
                )

    if collected_issues:
        raise ContractValidationError(collected_issues)

    return {"records": records, "review": review, "sources": sources}, normalizations
