# app.py 파일의 상단에 추가 (다른 import 문들 아래)

from hs_app import extract_schedule_from_table, extract_company_from_table, extract_supply_target_from_tables

# 또는 필요한 함수만 따로 불러올 수도 있습니다:
# from hs_app_BI import extract_company_from_table

import fitz  # PyMuPDF
import tempfile
import streamlit as st
import pdfplumber
import re
from io import BytesIO
from datetime import datetime
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict

# ============================
#  공통 유틸
# ============================
def parse_ymd(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y.%m.%d").date()
    except:
        return None


# ============================
#  단지명 추출
# ============================
def parse_complex_name(text: str):
    raw = None
    for line in text.splitlines():
        line = line.strip()
        if "입주자모집공고" in line:
            raw = line.replace("입주자모집공고", "").strip()
            break

    if not raw:
        return None

    name = re.sub(r"\s+", " ", raw)
    name = name.strip(" ,·-")
    return name or None


# ============================
#  공급위치 추출
# ============================
def parse_location(text: str):
    keywords = ["공급위치", "사업위치", "건설위치", "대지위치"]
    for line in text.splitlines():
        for key in keywords:
            if key in line:
                cleaned = line.replace(key, "")
                cleaned = cleaned.replace(":", "")
                cleaned = cleaned.replace("■", "")
                cleaned = cleaned.replace("위치", "").strip()
                cleaned = re.sub(r"\s+", " ", cleaned)
                return cleaned
    return None


# ============================
#  불필요 문단(유의사항·무주택기간 등) 제거
# ============================
def filter_irrelevant_sections(text: str) -> str:
    """
    모집공고 중 4~7, 11항목에서 자주 등장하는
    '유의사항/무주택기간 적용기준/기타 안내' 등은
    핵심정보 추출에 불필요하므로 분석 텍스트에서 제거한다.
    """
    remove_keywords = [
        "무주택기간 적용기준",
        "무주택 기간 적용기준",
        "무주택기간 산정기준",
        "청약 시 유의사항",
        "청약시 유의사항",
        "유의사항",
        "기타 사항",
        "기타사항",
        "공급(분양)계약에 관한 유의사항",
        "계약체결시 유의사항",
    ]

    filtered_lines = []
    for line in text.splitlines():
        s = line.strip()
        if any(k in s for k in remove_keywords):
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines)


# ============================
#  회사명 정규화 + 판별 유틸
# ============================
def normalize_company_name(name: str) -> str:
    if not name:
        return ""
    name = str(name)

    name = name.replace("\n", " ")
    name = re.sub(r"\s+", " ", name).strip()

    name = name.lstrip("※*•-·[]() ")

    name = name.replace(" (주)", "(주)").replace("(주) ", "(주)")
    name = name.replace(" ㈜", "㈜").replace("㈜ ", "㈜")

    if name.endswith("(주"):
        name = name + ")"
    if re.search(r"\(주$", name):
        name = name + ")"

    if "※" in name:
        name = name.split("※", 1)[0].strip()

    return name.strip()


COMPANY_HINT_KEYWORDS = [
    "조합", "건설", "주식회사", "㈜", "(주)", "개발",
    "디앤씨", "디엔씨", "산업", "엔지니어링",
    "홀딩스", "투자", "공사", "기업", "주택도시",
]


def looks_like_company(name: str) -> bool:
    if not name:
        return False
    name = name.strip()
    if len(name) > 30:
        return False

    bad_endings = ["기준", "적용기준", "적용 기준", "산정기준"]
    if any(name.endswith(be) for be in bad_endings):
        return False

    strong_keywords = ["조합", "건설", "주식회사", "㈜", "(주)", "개발", "공사", "기업"]
    if "기간" in name and not any(k in name for k in strong_keywords):
        return False

    if any(word in name for word in ["광역시", "특별시", "시 ", "군 ", "구 ", "동 ", "로 ", "길 "]):
        if not any(k in name for k in COMPANY_HINT_KEYWORDS):
            return False

    return any(k in name for k in COMPANY_HINT_KEYWORDS)


# ============================
#  텍스트 기반 시행/시공/분양 추출
# ============================
def extract_companies_from_text(text: str) -> Dict[str, List[str]]:
    result = {
        "시행사": [],
        "시공사": [],
        "분양대행사": [],
    }

    norm = text.replace("：", ":")
    norm = re.sub(r"\s+", " ", norm)

    patterns = {
        "시행사": [
            r"(?:사업주체|시행자|시행사)\s*[:]\s*([^\n:]+)",
        ],
        "시공사": [
            r"(?:시공자|시공사|시공)\s*[:]\s*([^\n:]+)",
        ],
        "분양대행사": [
            r"(?:분양대행사|분양대행|분양대리점)\s*[:]\s*([^\n:]+)",
        ],
    }

    for role, pats in patterns.items():
        for pat in pats:
            for m in re.finditer(pat, norm):
                name = normalize_company_name(m.group(1))
                if looks_like_company(name) and name not in result[role]:
                    result[role].append(name)

    simple_patterns = {
        "시행사": [
            r"(?:사업주체|시행자|시행사)\s+([^\n:]+)",
        ],
        "시공사": [
            r"(?:시공자|시공사|시공)\s+([^\n:]+)",
        ],
        "분양대행사": [
            r"(?:분양대행사|분양대행|분양대리점)\s+([^\n:]+)",
        ],
    }

    for role, pats in simple_patterns.items():
        for pat in pats:
            for m in re.finditer(pat, norm):
                name = normalize_company_name(m.group(1))
                if looks_like_company(name) and name not in result[role]:
                    result[role].append(name)

    combo_pattern = r"(시행|시공|분양대행)\s*[: ]\s*([^/]+)"
    for m in re.finditer(combo_pattern, norm):
        key = m.group(1)
        name = normalize_company_name(m.group(2))
        if "시행" in key:
            role = "시행사"
        elif "시공" in key:
            role = "시공사"
        else:
            role = "분양대행사"
        if looks_like_company(name) and name not in result[role]:
            result[role].append(name)

    return result


# ============================
#  핵심 정보(공급규모 + 텍스트 백업용 시행/시공) 추출
# ============================
def extract_core_info(text: str):
    info = {
        "공급규모": None,
        "시행사": None,
        "시공사": None,
    }

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue

        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "")
            cleaned = cleaned.replace("●", "")
            cleaned = cleaned.replace("공급규모", "")
            cleaned = cleaned.replace("총 공급세대수", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()
            info["공급규모"] = cleaned
            continue

        if not info["시행사"] and ("시행자" in s or "시행사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시행자", "").replace("시행사", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()

            cleaned = normalize_company_name(cleaned)
            if looks_like_company(cleaned):
                info["시행사"] = cleaned
            continue

        if not info["시공사"] and ("시공자" in s or "시공사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시공자", "").replace("시공사", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()

            cleaned = normalize_company_name(cleaned)
            if looks_like_company(cleaned):
                info["시공사"] = cleaned
            continue

    return info


# ============================
#  입주 예정일 추출
# ============================
def extract_move_in_date(text: str) -> str | None:
    candidate_lines: List[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue

        no_space = s.replace(" ", "")

        if any(k in no_space for k in ["입주시기", "입주시기", "입주예정", "입주예정일"]):
            candidate_lines.append(s)

    for s in candidate_lines:
        m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", s)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            return f"{year}년 {month}월"

        m2 = re.search(r"(\d{4})\.(\d{1,2})", s)
        if m2:
            year = int(m2.group(1))
            month = int(m2.group(2))
            return f"{year}년 {month}월"

    if candidate_lines:
        first = candidate_lines[0]
        return first[:40] + "..." if len(first) > 40 else first

    return None


# ============================
#  표 기반 회사정보 추출 유틸
# ============================
ROLE_KEYWORDS = {
    "시행사": ["사업주체", "시행자", "시행사", "사업시행자"],
    "시공사": ["시공사", "시공자", "시공"],
    "분양대행사": ["분양대행사", "분양대행", "분양대리점", "위탁사"],
}


def detect_role_from_header(text: str) -> List[str]:
    roles = []
    t = text.replace(" ", "")
    for role, keywords in ROLE_KEYWORDS.items():
        if any(k in t for k in keywords):
            roles.append(role)
    return roles


def extract_from_vertical_label_table(
    df: pd.DataFrame,
    page_idx: int,
) -> Dict[str, List[Tuple[str, int]]]:
    res = {
        "시행사": [],
        "시공사": [],
        "분양대행사": [],
    }
    if df.empty:
        return res

    df = df.fillna("")
    label_col = df.iloc[:, 0].astype(str)

    for i, label in enumerate(label_col):
        roles = detect_role_from_header(label)
        if not roles:
            continue
        row = df.iloc[i, 1:]
        candidates = [normalize_company_name(v) for v in row if str(v).strip()]
        for role in roles:
            for c in candidates:
                if looks_like_company(c):
                    res[role].append((c, page_idx))

    return res


def extract_from_horizontal_header_table(
    df: pd.DataFrame,
    page_idx: int,
) -> Dict[str, List[Tuple[str, int]]]:
    res = {
        "시행사": [],
        "시공사": [],
        "분양대행사": [],
    }
    if df.empty or len(df) < 2:
        return res

    df = df.fillna("")
    header = df.iloc[0].astype(str).tolist()
    body = df[1:]

    for col_idx, h in enumerate(header):
        roles = detect_role_from_header(h)
        if not roles:
            continue
        col_values = body.iloc[:, col_idx].astype(str)
        candidates = [
            normalize_company_name(v)
            for v in col_values
            if str(v).strip()
        ]
        for role in roles:
            for c in candidates:
                if looks_like_company(c):
                    res[role].append((c, page_idx))

    return res


def extract_company_candidates_from_pdf(pdf) -> Tuple[Dict[str, List[Tuple[str, int]]], int]:
    result = {
        "시행사": [],
        "시공사": [],
        "분양대행사": [],
    }

    last_page_idx = len(pdf.pages) - 1 if pdf.pages else 0

    for page_idx, page in enumerate(pdf.pages):
        tables = page.extract_tables() or []
        for table in tables:
            if not table:
                continue
            df = pd.DataFrame(table)
            if df.empty:
                continue

            vertical = extract_from_vertical_label_table(df, page_idx)
            horizontal = extract_from_horizontal_header_table(df, page_idx)

            for role in result.keys():
                result[role].extend(vertical.get(role, []))
                result[role].extend(horizontal.get(role, []))

    return result, last_page_idx


def choose_final_company(
    text_candidates: Dict[str, List[str]],
    table_candidates: Dict[str, List[Tuple[str, int]]],
    last_page_idx: int = None,
) -> Dict[str, str]:
    scores: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for role, vals in table_candidates.items():
        for name, page_idx in vals:
            if not name:
                continue
            base = 3
            bonus = 0
            if last_page_idx is not None and page_idx == last_page_idx:
                bonus += 2
            scores[role][name] += base + bonus

    for role, names in text_candidates.items():
        for name in names:
            if not name:
                continue
            scores[role][name] += 2

    final = {
        "시행사": "",
        "시공사": "",
        "분양대행사": "",
    }

    for role, name_scores in scores.items():
        if not name_scores:
            continue
        sorted_candidates = sorted(
            name_scores.items(),
            key=lambda x: (x[1], len(x[0])),
            reverse=True,
        )
        final[role] = sorted_candidates[0][0]

    return final


# ============================
#  텍스트 + 표 기반 통합 추출
# ============================
def extract_company_from_table(pdf, text: str) -> Dict[str, str]:
    text_candidates = extract_companies_from_text(text)
    table_candidates, last_page_idx = extract_company_candidates_from_pdf(pdf)
    final = choose_final_company(text_candidates, table_candidates, last_page_idx)
    return final


# ============================
#  중도금 대출 조건 추출
# ============================
def extract_loan_condition(text: str):
    condition = None
    related_lines = []

    for line in text.splitlines():
        s = line.strip()
        if "중도금" in s and "대출" in s:
            related_lines.append(s)
        elif "중도금" in s and "이자" in s:
            related_lines.append(s)

    joined = " ".join(related_lines)

    if "이자후불제" in joined or "이자 후불제" in joined:
        condition = "이자후불제"
    elif "무이자" in joined:
        condition = "무이자"

    if not condition and joined:
        condition = joined

    return condition


# ============================
#  엑셀 다운로드용 파일 생성
# ============================
def make_excel_file(
    complex_name: str,
    location: str,
    core: dict,
    move_in: str | None,
    final_siheng: str | None,
    final_sigong: str | None,
    final_agency: str | None,
    loan_cond: str | None,
    schedule_rows: list,
    supply_rows: list,
    price_rows: list,
) -> BytesIO:
    summary_rows = [
        {"항목": "단지명", "값": complex_name},
        {"항목": "공급위치", "값": location},
        {"항목": "공급규모", "값": core.get("공급규모") or ""},
        {"항목": "입주예정일", "값": move_in or ""},
        {"항목": "시행사", "값": final_siheng or ""},
        {"항목": "시공사", "값": final_sigong or ""},
        {"항목": "분양대행사", "값": final_agency or ""},
        {"항목": "중도금 대출 조건", "값": loan_cond or ""},
    ]

    for row in schedule_rows:
        summary_rows.append({"항목": row.get("항목", ""), "값": row.get("일정", "")})

    df_summary = pd.DataFrame(summary_rows)
    df_supply = pd.DataFrame(supply_rows) if supply_rows else pd.DataFrame()
    df_price = pd.DataFrame(price_rows) if price_rows else pd.DataFrame()

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = "모집공고"

        df_summary.to_excel(writer, index=False, sheet_name=sheet_name, startrow=0)

        start_row = len(df_summary) + 2

        if not df_supply.empty:
            df_supply.to_excel(writer, index=False, sheet_name=sheet_name, startrow=start_row)
            start_row += len(df_supply) + 2

        if not df_price.empty:
            df_price.to_excel(writer, index=False, sheet_name=sheet_name, startrow=start_row)

    output.seek(0)
    return output


# ============================
#  표에서 청약 일정 추출
# ============================
def extract_schedule_from_table(pdf):
    schedule = {
        "입주자모집공고일": None,
        "특별공급 접수일": None,
        "일반공급 1순위 접수일": None,
        "일반공급 2순위 접수일": None,
        "당첨자발표일": None,
        "서류접수": None,
        "계약체결": None,
    }

    header_map = {
        "입주자모집공고": "입주자모집공고일",
        "입주자 모집공고": "입주자모집공고일",
        "특별공급접수": "특별공급 접수일",
        "특별공급 신청": "특별공급 접수일",
        "특별공급 접수": "특별공급 접수일",
        "1순위 접수": "일반공급 1순위 접수일",
        "1순위": "일반공급 1순위 접수일",
        "일반공급 1순위 접수": "일반공급 1순위 접수일",
        "2순위 접수": "일반공급 2순위 접수일",
        "2순위": "일반공급 2순위 접수일",
        "일반공급 2순위 접수": "일반공급 2순위 접수일",
        "당첨자발표일": "당첨자발표일",
        "당첨자 발표": "당첨자발표일",
        "서류접수": "서류접수",
        "정당계약": "계약체결",
        "계약체결": "계약체결",
    }

    date_pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"

    def update(label, new_val):
        old = schedule.get(label)
        if not old:
            schedule[label] = new_val
            return

        try:
            old_d = datetime.strptime(re.findall(date_pattern, old)[0], "%Y.%m.%d")
            new_d = datetime.strptime(re.findall(date_pattern, new_val)[0], "%Y.%m.%d")
            if new_d > old_d:
                schedule[label] = new_val
        except:
            pass

    for page in pdf.pages:
        tables = page.extract_tables() or []

        for table in tables:
            if not table:
                continue

            rows = table

            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    if not cell:
                        continue
                    cell_t = cell.replace(" ", "")

                    for key, label in header_map.items():
                        if key.replace(" ", "") in cell_t:

                            for rr in range(r + 1, len(rows)):
                                if c >= len(rows[rr]):
                                    continue

                                raw = rows[rr][c] or ""
                                found = re.findall(date_pattern, raw)
                                if not found:
                                    continue

                                if label in ["서류접수", "계약체결"] and len(found) >= 2:
                                    update(label, f"{found[0]} ~ {found[-1]}")
                                else:
                                    update(label, found[0])

                                break

    return schedule


# ============================
#  공급대상(타입별) 추출
# ============================
def extract_supply_target_from_tables(pdf) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            if not table or len(table) < 3:
                continue

            df = pd.DataFrame(table).fillna("")
            header_idx = None

            for i, row in df.iterrows():
                row_txt = "".join(str(x) for x in row.tolist())
                if "주택형" in row_txt and ("약식표기" in row_txt or "약식 표기" in row_txt or "약식" in row_txt):
                    header_idx = i
                    break

            if header_idx is None:
                continue

            df2 = df.iloc[header_idx:].reset_index(drop=True)
            ncols = df2.shape[1]

            col_map: Dict[str, int] = {}
            for c in range(ncols):
                hdr = "".join(df2.iloc[0:4, c].astype(str).tolist())
                hdr = hdr.replace(" ", "").replace("\n", "")

                if "주택형" in hdr:
                    col_map["주택형"] = c
                elif "약식표기" in hdr or "약식표시" in hdr or "약식" in hdr:
                    col_map["약식표기"] = c
                elif "주거전용면적" in hdr or ("전용" in hdr and "면적" in hdr):
                    col_map["주거 전용면적"] = c
                elif "소계" in hdr and "세대" not in hdr:
                    col_map["주택공급면적 소계"] = c
                elif ("총공급" in hdr and "세대수" in hdr) or "총공급세대수" in hdr:
                    col_map["총 공급 세대수"] = c
                elif "일반공급" in hdr and "세대수" in hdr:
                    col_map["일반공급 세대수"] = c
                elif "기관추천" in hdr:
                    col_map["기관추천"] = c
                elif "다자녀" in hdr:
                    col_map["다자녀가구"] = c
                elif "신혼부부" in hdr:
                    col_map["신혼부부"] = c
                elif "노부모" in hdr:
                    col_map["노부모부양"] = c
                elif "생애최초" in hdr:
                    col_map["생애최초"] = c

            if not col_map:
                continue

            for r in range(1, df2.shape[0]):
                row = df2.iloc[r]
                row_txt = "".join(str(x) for x in row.tolist())

                if "합계" in row_txt:
                    continue

                def get_val(key: str) -> str:
                    idx = col_map.get(key)
                    if idx is None or idx >= len(row):
                        return ""
                    return str(row.iloc[idx]).strip()

                rec: Dict[str, str] = {}
                rec["주택형"] = get_val("주택형")
                rec["약식표기"] = get_val("약식표기")
                rec["주거 전용면적"] = get_val("주거 전용면적")
                rec["주택공급면적 소계"] = get_val("주택공급면적 소계")
                rec["총 공급 세대수"] = get_val("총 공급 세대수")
                rec["일반공급 세대수"] = get_val("일반공급 세대수")

                special_total = 0
                for k in ["기관추천", "다자녀가구", "신혼부부", "노부모부양", "생애최초"]:
                    idx = col_map.get(k)
                    if idx is None or idx >= len(row):
                        continue
                    raw = str(row.iloc[idx])
                    num = re.sub(r"[^0-9]", "", raw)
                    if num:
                        special_total += int(num)

                rec["특별공급 세대수"] = str(special_total) if special_total > 0 else ""

                if not (rec.get("주택형") or rec.get("약식표기")):
                    continue
                if ("주택형" in rec.get("주택형", "")) or ("약식" in rec.get("약식표기", "")):
                    continue

                results.append(rec)

            # 타입표는 한 번만 찾으면 충분하니, 첫 발견 후 종료
            if results:
                return results

    return results


# ============================
#  공급금액표 추출 (동·호·층별, 전체 타입)
# ============================
def extract_price_table_from_tables(pdf) -> List[Dict[str, str]]:
    """
    '공급금액표'에서
    - 주택형
    - 약식표기
    - 동/호별
    - 층구분
    - 해당세대수
    - 공급금액 소계
    를 뽑아온다.

    전략
    1) 옵션/선택사양 표는 제외
    2) 헤더에서 주택형/약식/동호/층/세대수 위치만 대략 잡고,
       공급금액 소계는 각 행에서 "가장 큰 금액"을 선택
    3) 세대수는 4자리(1000세대) 이상이면 잘못 인식된 금액으로 보고 버린다.
    """

    results: List[Dict[str, str]] = []

    last_col_map: Dict[str, int] | None = None
    current_type = ""
    current_abbr = ""
    current_dongho = ""

    def is_floor_like(s: str) -> bool:
        if not s:
            return False
        s2 = s.replace(" ", "")
        return ("층" in s2) and not ("동" in s2 or "호" in s2)

    for page_idx, page in enumerate(pdf.pages):
        tables = page.extract_tables() or []
        for table_idx, table in enumerate(tables):
            if not table or len(table) < 2:
                continue

            df = pd.DataFrame(table).fillna("")
            all_txt = "".join(df.astype(str).values.ravel()).replace(" ", "")

            # 1) 옵션/선택사양 표는 통째로 스킵
            if any(k in all_txt for k in ["옵션", "선택품목", "선택사양"]):
                continue

            # 2) 공급금액표 후보 필터
            has_price_word = ("공급금액" in all_txt) or ("분양금액" in all_txt)
            has_dongho = ("동" in all_txt and "호" in all_txt) or "동/호" in all_txt
            has_floor = "층구분" in all_txt or ("층" in all_txt and "구분" in all_txt)
            has_haedang = "해당세대" in all_txt

            if not has_price_word:
                continue
            if not (has_dongho or has_floor or has_haedang):
                continue

            # ---------- A. 완전한 헤더(주택형+약식표기) 찾기 ----------
            header_idx = None
            for i, row in df.iterrows():
                row_txt = "".join(str(x) for x in row.tolist())
                if (
                    "주택형" in row_txt
                    and ("약식표기" in row_txt or "약식 표기" in row_txt or "약식" in row_txt)
                ):
                    header_idx = i
                    break

            col_map: Dict[str, int] = {}

            if header_idx is not None:
                df2 = df.iloc[header_idx:].reset_index(drop=True)
                ncols = df2.shape[1]

                for c in range(ncols):
                    hdr = "".join(df2.iloc[0:4, c].astype(str).tolist())
                    hdr = hdr.replace(" ", "").replace("\n", "")

                    if "주택형" in hdr:
                        col_map["주택형"] = c
                    elif "약식표기" in hdr or "약식표시" in hdr or "약식" in hdr:
                        col_map["약식표기"] = c
                    elif ("동" in hdr and "호" in hdr) or "동/호" in hdr:
                        col_map["동/호별"] = c
                    elif "층구분" in hdr or ("층" in hdr and "구분" in hdr):
                        col_map["층구분"] = c
                    elif "해당세대수" in hdr or "해당세대" in hdr:
                        col_map["해당세대수"] = c

                last_col_map = col_map.copy()

            else:
                # ---------- B. 헤더 없는 이어지는 표(7~9페이지) ----------
                if not last_col_map:
                    continue

                df2 = df.reset_index(drop=True)
                col_map = last_col_map.copy()

                # 상단 몇 줄을 스캔해 동/층/세대 위치 보정
                tmp_map: Dict[str, int] = {}
                max_head_rows = min(5, df2.shape[0])

                for c in range(df2.shape[1]):
                    pieces = []
                    for r_head in range(max_head_rows):
                        pieces.append(str(df2.iloc[r_head, c]))
                    hdr = "".join(pieces).replace(" ", "").replace("\n", "")

                    if ("동" in hdr and "호" in hdr) or "동/호" in hdr:
                        tmp_map["동/호별"] = c
                    elif "층구분" in hdr or ("층" in hdr and "구분" in hdr):
                        tmp_map["층구분"] = c
                    elif "해당세대수" in hdr or "해당세대" in hdr:
                        tmp_map["해당세대수"] = c

                col_map.update(tmp_map)

            if not col_map:
                continue

            # ---------------------- 데이터 행 파싱 ----------------------
            start_row = 1 if header_idx is not None else 0

            for r in range(start_row, df2.shape[0]):
                row = df2.iloc[r]
                row_txt = "".join(str(x) for x in row.tolist())

                if "주택형" in row_txt and ("약식표기" in row_txt or "약식" in row_txt):
                    continue
                if "합계" in row_txt or "전타입" in row_txt or "부분" in row_txt:
                    continue


# ============================
#  공급금액표 추출 (동·호·층별, 전체 타입)
# ============================
def extract_price_table_from_tables(pdf) -> List[Dict[str, str]]:
    """
    '공급금액표'에서
    - 주택형
    - 약식표기
    - 동/호별
    - 층구분
    - 해당세대수
    - 공급금액 소계
    를 뽑아온다.

    전략
    1) 옵션/선택사양 표는 제외
    2) 헤더에서 주택형/약식/동호/층/세대수 위치만 대략 잡고,
       공급금액 소계는 각 행에서 "가장 큰 금액"을 선택
    3) (주택형, 약식표기, 동/호별, 층구분) 이 같은 행들 중에서
       공급금액 소계가 가장 큰 한 행만 최종 결과에 남긴다.
    """

    results: List[Dict[str, str]] = []

    last_col_map: Dict[str, int] | None = None
    current_type = ""
    current_abbr = ""
    current_dongho = ""

    def is_floor_like(s: str) -> bool:
        if not s:
            return False
        s2 = s.replace(" ", "")
        return ("층" in s2) and not ("동" in s2 or "호" in s2)

    for page_idx, page in enumerate(pdf.pages):
        tables = page.extract_tables() or []
        for table_idx, table in enumerate(tables):
            if not table or len(table) < 2:
                continue

            df = pd.DataFrame(table).fillna("")
            all_txt = "".join(df.astype(str).values.ravel()).replace(" ", "")

            # 1) 옵션/선택사양 표는 통째로 스킵
            if any(k in all_txt for k in ["옵션", "선택품목", "선택사양"]):
                continue

            # 2) 공급금액표 후보 필터
            has_price_word = ("공급금액" in all_txt) or ("분양금액" in all_txt)
            has_dongho = ("동" in all_txt and "호" in all_txt) or "동/호" in all_txt
            has_floor = "층구분" in all_txt or ("층" in all_txt and "구분" in all_txt)
            has_haedang = "해당세대" in all_txt

            if not has_price_word:
                # 공급금액 관련 단어가 없으면 다른 표일 가능성이 큼
                continue
            if not (has_dongho or has_floor or has_haedang):
                continue

            # ---------------- A. 완전한 헤더(주택형+약식표기) 탐색 ----------------
            header_idx = None
            for i, row in df.iterrows():
                row_txt = "".join(str(x) for x in row.tolist())
                if (
                    "주택형" in row_txt
                    and ("약식표기" in row_txt or "약식 표기" in row_txt or "약식" in row_txt)
                ):
                    header_idx = i
                    break

            col_map: Dict[str, int] = {}

            if header_idx is not None:
                df2 = df.iloc[header_idx:].reset_index(drop=True)
                ncols = df2.shape[1]

                for c in range(ncols):
                    hdr = "".join(df2.iloc[0:4, c].astype(str).tolist())
                    hdr = hdr.replace(" ", "").replace("\n", "")

                    if "주택형" in hdr:
                        col_map["주택형"] = c
                    elif "약식표기" in hdr or "약식표시" in hdr or "약식" in hdr:
                        col_map["약식표기"] = c
                    elif ("동" in hdr and "호" in hdr) or "동/호" in hdr:
                        col_map["동/호별"] = c
                    elif "층구분" in hdr or ("층" in hdr and "구분" in hdr):
                        col_map["층구분"] = c
                    elif "해당세대수" in hdr or "해당세대" in hdr:
                        col_map["해당세대수"] = c

                last_col_map = col_map.copy()

            else:
                # ------------- B. 헤더 없는 이어지는 표(7~9페이지) -------------
                if not last_col_map:
                    continue

                df2 = df.reset_index(drop=True)
                ncols = df2.shape[1]

                col_map = last_col_map.copy()

                # 상단 몇 줄을 스캔해 동/층/세대 위치만 보정
                tmp_map: Dict[str, int] = {}
                max_head_rows = min(5, df2.shape[0])

                for c in range(ncols):
                    pieces = []
                    for r_head in range(max_head_rows):
                        pieces.append(str(df2.iloc[r_head, c]))
                    hdr = "".join(pieces).replace(" ", "").replace("\n", "")

                    if ("동" in hdr and "호" in hdr) or "동/호" in hdr:
                        tmp_map["동/호별"] = c
                    elif "층구분" in hdr or ("층" in hdr and "구분" in hdr):
                        tmp_map["층구분"] = c
                    elif "해당세대수" in hdr or "해당세대" in hdr:
                        tmp_map["해당세대수"] = c

                col_map.update(tmp_map)
                df2 = df2  # 이름 맞추기용

            if not col_map:
                continue

            # ---------------------- 데이터 행 파싱 ----------------------
            start_row = 1 if header_idx is not None else 0

            for r in range(start_row, df2.shape[0]):
                row = df2.iloc[r]
                row_txt = "".join(str(x) for x in row.tolist())

                if "주택형" in row_txt and ("약식표기" in row_txt or "약식" in row_txt):
                    continue
                if "합계" in row_txt or "전타입" in row_txt or "부분" in row_txt:
                    continue

                def get_val_idx(row, idx: int | None) -> str:
                    if idx is None or idx < 0 or idx >= len(row):
                        return ""
                    return str(row.iloc[idx]).strip()

                # 주택형 / 약식표기
                idx_type = col_map.get("주택형")
                v_type = get_val_idx(row, idx_type)
                if v_type:
                    current_type = v_type

                idx_abbr = col_map.get("약식표기")
                v_abbr = get_val_idx(row, idx_abbr)
                if v_abbr:
                    current_abbr = v_abbr

                # 동/호 / 층 처리
                idx_dongho = col_map.get("동/호별")
                raw_dongho = get_val_idx(row, idx_dongho)

                floor_val = get_val_idx(row, col_map.get("층구분"))

                if raw_dongho:
                    if is_floor_like(raw_dongho):
                        # 동/호 칸에 층 정보가 들어온 경우 → 동/호는 이전값 유지, 층으로 사용
                        if not is_floor_like(floor_val):
                            floor_val = raw_dongho
                    else:
                        current_dongho = raw_dongho

                # 층구분에 '층' 글자가 빠졌으면 보정
                if floor_val:
                    fv = floor_val.replace(" ", "")
                    if ("층" not in fv) and re.search(r"\d", fv):
                        floor_val = fv + "층"

                # 세대수
                haedang_val = get_val_idx(row, col_map.get("해당세대수"))
                # 너무 큰 숫자는 세대수로 보기 어려우므로 버림
                hae_digits = re.sub(r"[^0-9]", "", haedang_val or "")
                if hae_digits and len(hae_digits) > 3:  # 999세대 초과면 이상치로 처리
                    haedang_val = ""

                # ----- 공급금액 소계: 행의 우측 부분에서 가장 큰 금액 선택 -----
                # 시작 인덱스: 세대수 다음 컬럼 또는 층구분 다음 컬럼
                candidate_start = 0
                if col_map.get("해당세대수") is not None:
                    candidate_start = col_map["해당세대수"] + 1
                elif col_map.get("층구분") is not None:
                    candidate_start = col_map["층구분"] + 1

                max_price_int = 0
                price_val = ""

                for c_idx in range(candidate_start, len(row)):
                    cell = get_val_idx(row, c_idx)
                    digits = re.sub(r"[^0-9]", "", cell)
                    if not digits:
                        continue
                    val_int = int(digits)
                    # 너무 작은 값(예: 100,000 이하는 옵션/수수료일 가능성이 큼)
                    if val_int <= 100000:
                        continue
                    if val_int > max_price_int:
                        max_price_int = val_int
                        price_val = cell

                # 공급금액 소계가 없으면 이 행은 스킵
                if max_price_int == 0:
                    continue

                # 동/호, 층, 세대수 셋 다 비어 있으면 버림
                if not (current_dongho or floor_val or haedang_val):
                    continue
                # 타입 정보도 없으면 버림
                if not (current_type or current_abbr):
                    continue

                rec: Dict[str, str] = {
                    "주택형": current_type,
                    "약식표기": current_abbr,
                    "동/호별": current_dongho,
                    "층구분": floor_val,
                    "해당세대수": haedang_val,
                    "공급금액 소계": price_val,
                }

                results.append(rec)

    # --------- (정리 단계) 같은 동/호/층 조합 중 공급금액이 가장 큰 행만 남기기 ---------
    dedup: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}

    for rec in results:
        key = (
            rec.get("주택형", ""),
            rec.get("약식표기", ""),
            rec.get("동/호별", ""),
            rec.get("층구분", ""),
        )
        price_digits = re.sub(r"[^0-9]", "", rec.get("공급금액 소계", "") or "0")
        price_int = int(price_digits) if price_digits else 0

        if key not in dedup:
            dedup[key] = rec
        else:
            old_price_digits = re.sub(
                r"[^0-9]", "", dedup[key].get("공급금액 소계", "") or "0"
            )
            old_price_int = int(old_price_digits) if old_price_digits else 0
            if price_int > old_price_int:
                dedup[key] = rec

    final_rows = list(dedup.values())
    return final_rows

def extract_price_table_with_layout(uploaded) -> List[Dict[str, str]]:
    """
    PyMuPDF(레이아웃 기반)으로 공급금액표(동·호·층별)를 추출한다.
    - 주택형
    - 약식표기
    - 동/호별
    - 층구분
    - 해당세대수
    - 공급금액 소계
    """

    # 1) 업로드된 PDF를 임시 파일로 저장
    uploaded.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name

    doc = fitz.open(tmp_path)

    results: List[Dict[str, str]] = []

    # 컬럼별 x센터 좌표 (헤더에서 한 번 잡으면 이후 페이지에서도 재사용)
    col_centers: Dict[str, float] = {}
    header_y: float | None = None

    # forward-fill용 상태
    current_type = ""
    current_abbr = ""
    current_dongho = ""

    # y값 기준으로 행 묶는 헬퍼
    def group_by_y(words, tol=2.0):
        """[(x,y,text), ...] -> [(y_center, [words...]), ...]"""
        words_sorted = sorted(words, key=lambda w: w["y"])
        rows = []
        cur = []
        last_y = None
        for w in words_sorted:
            if last_y is None or abs(w["y"] - last_y) <= tol:
                cur.append(w)
                last_y = w["y"] if last_y is None else (last_y + w["y"]) / 2
            else:
                rows.append((last_y, cur))
                cur = [w]
                last_y = w["y"]
        if cur:
            rows.append((last_y, cur))
        return rows

    def is_floor_like(s: str) -> bool:
        if not s:
            return False
        t = s.replace(" ", "")
        return "층" in t and not ("동" in t or "호" in t)

    for page in doc:
        page_text = page.get_text()
        # 공급금액표가 없는 페이지는 스킵
        if "공급금액" not in page_text or "소계" not in page_text:
            continue

        # PyMuPDF words: [x0, y0, x1, y1, text, block_no, line_no, word_no]
        raw_words = page.get_text("words") or []
        words = [
            {
                "x": (w[0] + w[2]) / 2.0,
                "y": (w[1] + w[3]) / 2.0,
                "text": str(w[4]).strip(),
            }
            for w in raw_words
            if str(w[4]).strip()
        ]

        if not words:
            continue

        rows = group_by_y(words, tol=2.0)

        # ---- 1) 헤더 행 찾기 (있으면 컬럼 위치 갱신) ----
        header_found_here = False
        for y_center, row_words in rows:
            row_text = "".join(w["text"] for w in row_words)
            if "주택형" in row_text and ("약식표기" in row_text or "약식" in row_text):
                header_found_here = True
                header_y = y_center

                # 새로 col_centers 설정
                new_centers: Dict[str, float] = {}
                for w in row_words:
                    t = w["text"].replace(" ", "")
                    x = w["x"]
                    if "주택형" in t:
                        new_centers["주택형"] = x
                    elif "약식" in t:
                        new_centers["약식표기"] = x
                    elif "동" in t and "호" in t:
                        new_centers["동/호별"] = x
                    elif "층구분" in t or ("층" in t and "구분" in t):
                        new_centers["층구분"] = x
                    elif "해당세대" in t:
                        new_centers["해당세대수"] = x
                    elif "공급금액" in t and "소계" in t:
                        new_centers["공급금액 소계"] = x

                # 공급금액 소계 못 찾으면 가장 오른쪽에 있는 헤더를 가격으로 사용
                if "공급금액 소계" not in new_centers:
                    right_word = max(row_words, key=lambda w: w["x"])
                    new_centers["공급금액 소계"] = right_word["x"]

                col_centers = new_centers
                break

        # 이 페이지에 헤더가 없고, 이전에도 col_centers를 못 잡았으면 스킵
        if not col_centers:
            continue

        # ---- 2) 실제 데이터 행 처리 ----
        # header_y 기준으로 그 아래만 데이터 행으로 봄
        data_rows = []
        for y_center, row_words in rows:
            if header_y is not None and y_center <= header_y + 1:
                continue
            data_rows.append((y_center, row_words))

        for y_center, row_words in data_rows:
            row_text = "".join(w["text"] for w in row_words)

            # 옵션표/합계/전타입 등은 스킵
            if any(k in row_text for k in ["합계", "전타입", "부분", "옵션", "선택품목", "선택사양"]):
                continue

            # 이 행에서 특정 컬럼(x센터)에 가장 가까운 단어 찾아주는 헬퍼
            def pick_nearest(col_name: str) -> str:
                if col_name not in col_centers:
                    return ""
                cx = col_centers[col_name]
                best = None
                best_diff = None
                for w in row_words:
                    diff = abs(w["x"] - cx)
                    # 너무 떨어진 건 같은 열이 아니라고 판단 (50pt 정도 기준)
                    if diff > 50:
                        continue
                    if best is None or diff < best_diff:
                        best = w
                        best_diff = diff
                return best["text"] if best is not None else ""

            v_type = pick_nearest("주택형")
            v_abbr = pick_nearest("약식표기")
            v_dongho = pick_nearest("동/호별")
            v_floor = pick_nearest("층구분")
            v_haedang = pick_nearest("해당세대수")
            v_price = pick_nearest("공급금액 소계")

            # forward-fill
            if v_type:
                current_type = v_type
            if v_abbr:
                current_abbr = v_abbr
            if v_dongho:
                current_dongho = v_dongho

            # 동/호 대신 층이 들어왔으면 보정
            if is_floor_like(v_dongho) and not is_floor_like(v_floor):
                v_floor = v_dongho
                v_dongho = current_dongho

            # 층표시에 '층' 글자 없으면 붙여주기
            if v_floor:
                fv = v_floor.replace(" ", "")
                if "층" not in fv and re.search(r"\d", fv):
                    v_floor = fv + "층"

            # 세대수: 4자리(1000세대) 이상이면 금액으로 보고 제거
            if v_haedang:
                digits = re.sub(r"[^0-9]", "", v_haedang)
                if digits and len(digits) > 3:
                    v_haedang = ""

            # 공급금액: 너무 작은 금액(<= 1,000,000)은 면적/수수료로 보고 무시
            if v_price:
                p_digits = re.sub(r"[^0-9]", "", v_price)
                if not p_digits or int(p_digits) <= 1000000:
                    v_price = ""

            # 최소한의 정보 체크
            if not (current_type or current_abbr):
                continue
            if not (current_dongho or v_floor or v_haedang):
                continue
            if not v_price:
                # 가격이 비었으면 이 행은 버린다 (필요하면 나중에 보완)
                continue

            rec: Dict[str, str] = {
                "주택형": current_type,
                "약식표기": current_abbr,
                "동/호별": current_dongho,
                "층구분": v_floor,
                "해당세대수": v_haedang,
                "공급금액 소계": v_price,
            }
            results.append(rec)

    return results


# ============================
# Streamlit UI
# ============================
st.set_page_config(page_title="입주자모집공고 분석기", layout="wide")

st.sidebar.title("📂 PDF 업로드")
uploaded = st.sidebar.file_uploader("PDF 파일을 업로드하세요", type=["pdf"])

st.title("🏢 입주자모집공고 분석기 (자동 분석)")

if uploaded:
    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        table_company = extract_company_from_table(pdf, text)
        supply_rows = extract_supply_target_from_tables(pdf)

    # 🔽 공급금액표: PyMuPDF 레이아웃 엔진 우선, 안 되면 기존 pdfplumber 버전 사용
    try:
        price_rows = extract_price_table_with_layout(uploaded)
        if not price_rows:
            uploaded.seek(0)
            with pdfplumber.open(uploaded) as pdf2:
                price_rows = extract_price_table_from_tables(pdf2)
    except Exception:
        uploaded.seek(0)
        with pdfplumber.open(uploaded) as pdf2:
            price_rows = extract_price_table_from_tables(pdf2)


    core = extract_core_info(text)
    loan_cond = extract_loan_condition(text)
    move_in = extract_move_in_date(text)

    st.subheader("🧠 자동 분석 결과")

    st.markdown(f"**🏢 단지명:** {parse_complex_name(text) or '정보 없음'}")
    st.markdown(f"**📍 공급 위치:** {parse_location(text) or '정보 없음'}")

    st.subheader("📌 핵심 정보 요약")
    st.write(f"- **공급규모:** {core.get('공급규모') or '정보 없음'}")
    st.write(f"- **입주예정일:** {move_in or '정보 없음'}")

    final_siheng = table_company.get("시행사") or core.get("시행사")
    st.write(f"- **시행사:** {final_siheng or '정보 없음'}")

    final_sigong = table_company.get("시공사") or core.get("시공사")
    st.write(f"- **시공사:** {final_sigong or '정보 없음'}")

    final_agency = table_company.get("분양대행사")
    if final_agency:
        st.write(f"- **분양대행사:** {final_agency}")

    st.write(f"- **중도금 대출 조건:** {loan_cond or '정보 없음'}")

    st.subheader("📅 청약 일정 자동 분류")

    order = [
        "입주자모집공고일",
        "특별공급 접수일",
        "일반공급 1순위 접수일",
        "일반공급 2순위 접수일",
        "당첨자발표일",
        "서류접수",
        "계약체결",
    ]

    rows = []
    for key in order:
        val = schedule.get(key)
        rows.append({"항목": key, "일정": val or "정보 없음"})
        st.write(f"- **{key}**: {val or '정보 없음'}")

    df_schedule = pd.DataFrame(rows)
    st.table(df_schedule)

    st.subheader("🏠 공급대상 (타입별 요약)")
    if supply_rows:
        df_supply = pd.DataFrame(supply_rows)
        st.table(df_supply)
    else:
        st.info("공급대상 표를 찾지 못했습니다.")

    st.subheader("💰 공급금액표 (동·호·층별)")
    if price_rows:
        df_price = pd.DataFrame(price_rows)
        st.table(df_price)
    else:
        st.info("공급금액표를 찾지 못했습니다.")

    complex_name = parse_complex_name(text) or ""
    location = parse_location(text) or ""

    # 엑셀용으로도 불필요한 컬럼 정리 (혹시라도 생길 경우 대비)
    clean_price_rows = []
    for row in price_rows:
        clean = {
            "주택형": row.get("주택형", ""),
            "약식표기": row.get("약식표기", ""),
            "동/호별": row.get("동/호별", ""),
            "층구분": row.get("층구분", ""),
            "해당세대수": row.get("해당세대수", ""),
            "공급금액 소계": row.get("공급금액 소계", ""),
        }
        clean_price_rows.append(clean)

    excel_bytes = make_excel_file(
        complex_name=complex_name,
        location=location,
        core=core,
        move_in=move_in,
        final_siheng=final_siheng,
        final_sigong=final_sigong,
        final_agency=final_agency,
        loan_cond=loan_cond,
        schedule_rows=rows,
        supply_rows=supply_rows,
        price_rows=clean_price_rows,
    )

    st.download_button(
        label="📥 엑셀 다운로드",
        data=excel_bytes,
        file_name=f"{complex_name or '분양단지'}_모집공고_자동분석.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

else:
    st.info("PDF 파일을 업로드해주세요.")
