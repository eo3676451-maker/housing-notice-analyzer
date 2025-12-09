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

    🔹 특징
    1) 옵션표(선택옵션, 확장비 등)는 전부 제외
    2) 1개 타입이 여러 페이지/여러 테이블로 끊어져 있어도 이어서 수집
       (6페이지 헤더 + 7~9페이지 본문처럼)
    3) '부분', '전 타입' 같은 요약행 / 면적표는 제거
    """

    results: List[Dict[str, str]] = []

    # 이전에 성공적으로 파싱한 "완전한 헤더" 테이블의 컬럼 매핑
    last_col_map: Dict[str, int] | None = None
    current_type = ""
    current_abbr = ""

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
            has_price = ("공급금액" in all_txt and "소계" in all_txt)
            has_dongho = ("동" in all_txt and "호" in all_txt) or "동/호" in all_txt
            has_floor = "층구분" in all_txt or ("층" in all_txt and "구분" in all_txt)
            has_haedang = "해당세대" in all_txt

            # 헤더(공급금액 소계)가 한 번도 안 나온 상태에서 has_price=False 면 스킵
            if not has_price and not last_col_map:
                continue

            # 동/층/해당세대 관련 단서가 전혀 없으면 그냥 요약표일 가능성이 큼
            if not (has_dongho or has_floor or has_haedang):
                continue

            # ---------------------------------------------------
            # A. '주택형 + 약식표기'가 같이 있는 표 (완전한 헤더)
            # ---------------------------------------------------
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
                # ✅ "완전한 헤더" 테이블 (예: 6페이지)
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
                    elif "공급금액" in hdr and "소계" in hdr:
                        col_map["공급금액 소계"] = c
                    elif "소계" in hdr and "공급금액 소계" not in col_map:
                        col_map["공급금액 소계"] = c

                if not col_map.get("공급금액 소계"):
                    # 공급금액 위치를 못 찾으면 이 표는 스킵
                    continue

                # 이후 이어지는 표에서 재사용할 기준
                last_col_map = col_map.copy()

            else:
                # ---------------------------------------------------
                # B. '헤더 없는 이어지는 표' (ex. 7~9페이지)
                #    → 직전에 본 col_map(완전한 헤더)을 재사용
                #    → 7페이지처럼 머리글 행이 애매한 경우를 위해 0~4행까지 폭넓게 스캔
                # ---------------------------------------------------
                if not last_col_map:
                    # 아직 기준 헤더를 본 적이 없으면 건너뜀
                    continue

                df2 = df.reset_index(drop=True)
                ncols = df2.shape[1]

                col_map = last_col_map.copy()

                # 이어지는 표 상단(0~4행)에서 다시 한 번 동/층/세대/소계 위치를 보정
                tmp_map: Dict[str, int] = {}
                max_head_rows = min(5, df2.shape[0])

                for c in range(ncols):
                    pieces = []
                    for r_head in range(max_head_rows):
                        pieces.append(str(df2.iloc[r_head, c]))
                    hdr = "".join(pieces)
                    hdr = hdr.replace(" ", "").replace("\n", "")

                    if ("동" in hdr and "호" in hdr) or "동/호" in hdr:
                        tmp_map["동/호별"] = c
                    elif "층구분" in hdr or ("층" in hdr and "구분" in hdr):
                        tmp_map["층구분"] = c
                    elif "해당세대수" in hdr or "해당세대" in hdr:
                        tmp_map["해당세대수"] = c
                    elif "공급금액" in hdr and "소계" in hdr:
                        tmp_map["공급금액 소계"] = c

                # 발견된 게 있으면 기준 맵 덮어쓰기
                col_map.update(tmp_map)

                # 그래도 핵심 컬럼이 하나도 안 잡히면 이 표는 포기
                if not (col_map.get("동/호별") or col_map.get("층구분") or col_map.get("해당세대수")):
                    continue
                if not col_map.get("공급금액 소계"):
                    continue

            # ---------------------------------------------------
            # 데이터 행 파싱
            # ---------------------------------------------------
            start_row = 1 if header_idx is not None else 0

            for r in range(start_row, df2.shape[0]):
                row = df2.iloc[r]
                row_txt = "".join(str(x) for x in row.tolist())

                # 중간에 또 나오는 헤더 / 합계 행은 스킵
                if "주택형" in row_txt and ("약식표기" in row_txt or "약식" in row_txt):
                    continue
                if "합계" in row_txt:
                    continue

                def get_val(idx: int | None) -> str:
                    if idx is None or idx >= len(row):
                        return ""
                    return str(row.iloc[idx]).strip()

                # 주택형 / 약식표기 → forward-fill
                idx_type = col_map.get("주택형")
                if idx_type is not None:
                    v = get_val(idx_type)
                    if v:
                        current_type = v

                idx_abbr = col_map.get("약식표기")
                if idx_abbr is not None:
                    v = get_val(idx_abbr)
                    if v:
                        current_abbr = v

                rec: Dict[str, str] = {
                    "주택형": current_type,
                    "약식표기": current_abbr,
                    "동/호별": get_val(col_map.get("동/호별")),
                    "층구분": get_val(col_map.get("층구분")),
                    "해당세대수": get_val(col_map.get("해당세대수")),
                    "공급금액 소계": get_val(col_map.get("공급금액 소계")),
                }

                # 🔹 진짜 공급금액 행만 남기기 위한 필터들 🔹

                # 1) 동/호, 층, 해당세대수 셋 다 비어 있으면 요약행으로 보고 제거
                if not (rec["동/호별"] or rec["층구분"] or rec["해당세대수"]):
                    continue

                # 2) 공급금액 소계가 너무 작은 숫자(면적 등)면 제거
                amt_digits = re.sub(r"[^0-9]", "", rec["공급금액 소계"] or "")
                if not amt_digits:
                    continue
                if len(amt_digits) <= 6:
                    continue

                # 3) 타입 정보가 전혀 없으면 (진짜로 이상한 행) 제거
                if not rec["주택형"] and not rec["약식표기"]:
                    continue

                results.append(rec)

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

    🔹 특징
    1) 옵션표(선택옵션, 확장비 등)는 전부 제외
    2) 1개 타입이 여러 페이지/여러 테이블로 끊어져 있어도 이어서 수집
       (6페이지 헤더 + 7~9페이지 본문처럼)
    3) '부분', '전 타입' 같은 요약행 / 면적표는 제거
    """

    results: List[Dict[str, str]] = []

    last_col_map: Dict[str, int] | None = None
    current_type = ""
    current_abbr = ""
    current_dongho = ""  # 동/호별 forward-fill

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
            has_price = ("공급금액" in all_txt and "소계" in all_txt)
            has_dongho = ("동" in all_txt and "호" in all_txt) or "동/호" in all_txt
            has_floor = "층구분" in all_txt or ("층" in all_txt and "구분" in all_txt)
            has_haedang = "해당세대" in all_txt

            if not has_price and not last_col_map:
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
                    elif "공급금액" in hdr and "소계" in hdr:
                        # '공급금액 소계'가 명시된 컬럼 → 일단 후보로 설정
                        col_map["공급금액 소계"] = c
                    elif "소계" in hdr:
                        # 여러 '소계' 중에서는 항상 가장 오른쪽(마지막)을 사용
                        col_map["공급금액 소계"] = c

                if "공급금액 소계" not in col_map:
                    continue

                last_col_map = col_map.copy()

            else:
                # ------------- B. 헤더 없는 이어지는 표(7~9페이지) -------------
                if not last_col_map:
                    continue

                df2 = df.reset_index(drop=True)
                ncols = df2.shape[1]

                col_map = last_col_map.copy()

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
                    elif "공급금액" in hdr and "소계" in hdr:
                        tmp_map["공급금액 소계"] = c
                    elif "소계" in hdr:
                        tmp_map["공급금액 소계"] = c  # 가장 오른쪽 소계가 덮어쓰게 함

                col_map.update(tmp_map)

                if not (col_map.get("동/호별") or col_map.get("층구분") or col_map.get("해당세대수")):
                    continue
                if "공급금액 소계" not in col_map:
                    continue

            # ---------------------- 데이터 행 파싱 ----------------------
            start_row = 1 if header_idx is not None else 0

            for r in range(start_row, df2.shape[0]):
                row = df2.iloc[r]
                row_txt = "".join(str(x) for x in row.tolist())

                if "주택형" in row_txt and ("약식표기" in row_txt or "약식" in row_txt):
                    continue
                if "합계" in row_txt:
                    continue

                def get_val(idx: int | None) -> str:
                    if idx is None or idx >= len(row):
                        return ""
                    return str(row.iloc[idx]).strip()

                # 주택형 / 약식표기
                idx_type = col_map.get("주택형")
                if idx_type is not None:
                    v = get_val(idx_type)
                    if v:
                        current_type = v

                idx_abbr = col_map.get("약식표기")
                if idx_abbr is not None:
                    v = get_val(idx_abbr)
                    if v:
                        current_abbr = v

                # 동/호 / 층 처리
                idx_dongho = col_map.get("동/호별")
                raw_dongho = get_val(idx_dongho) if idx_dongho is not None else ""

                floor_val = get_val(col_map.get("층구분")) if col_map.get("층구분") is not None else ""

                if raw_dongho:
                    if is_floor_like(raw_dongho):
                        # 동/호 칸에 층 정보가 들어온 경우 → 동/호는 이전값 유지, 층으로 사용
                        if not is_floor_like(floor_val):
                            floor_val = raw_dongho
                    else:
                        current_dongho = raw_dongho

                # 층구분에 '층' 글자가 빠져 있으면 보정 (숫자 또는 범위만 있을 때)
                if floor_val:
                    fv = floor_val.replace(" ", "")
                    if ("층" not in fv) and re.search(r"\d", fv):
                        floor_val = fv + "층"

                haedang_val = get_val(col_map.get("해당세대수")) if col_map.get("해당세대수") is not None else ""
                price_val = get_val(col_map.get("공급금액 소계"))

                # 공급금액이 면적처럼 너무 작으면 제거
                amt_digits = re.sub(r"[^0-9]", "", price_val or "")
                if not amt_digits or len(amt_digits) <= 6:
                    continue

                rec: Dict[str, str] = {
                    "주택형": current_type,
                    "약식표기": current_abbr,
                    "동/호별": current_dongho,
                    "층구분": floor_val,
                    "해당세대수": haedang_val,
                    "공급금액 소계": price_val,
                }

                # 동/호, 층, 해당세대수 셋 다 비면 버림
                if not (rec["동/호별"] or rec["층구분"] or rec["해당세대수"]):
                    continue
                if not rec["주택형"] and not rec["약식표기"]:
                    continue

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
    text = ""
    with pdfplumber.open(uploaded) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"

    text = filter_irrelevant_sections(text)

    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        table_company = extract_company_from_table(pdf, text)
        supply_rows = extract_supply_target_from_tables(pdf)
        price_rows = extract_price_table_from_tables(pdf)

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
