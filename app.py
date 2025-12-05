import streamlit as st
import pdfplumber
import re
from datetime import datetime
import pandas as pd
from io import BytesIO

# ============================
#  공통 유틸
# ============================
def parse_ymd(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y.%m.%d").date()
    except:
        return None


# ============================
#  분석 함수 1: 단지명 추출
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
#  분석 함수 2: 공급위치 추출
# ============================
def parse_location(text: str):
    keywords = ["공급위치", "사업위치", "건설위치", "대지위치"]
    for line in text.splitlines():
        for key in keywords:
            if key in line:
                cleaned = line
                cleaned = cleaned.replace(key, "")
                cleaned = cleaned.replace("■", "").replace("●", "")
                cleaned = cleaned.replace("위치", "")
                cleaned = cleaned.replace(":", "").replace("：", "")
                cleaned = cleaned.strip()
                cleaned = re.sub(r"\s+", " ", cleaned)
                return cleaned
    return None


# ============================
#  분석 함수 3: 텍스트에서 핵심 정보 추출
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

        # --- 공급규모 ---
        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            # '공급규모', '총 공급세대수' 여러 번 있어도 전부 제거
            cleaned = re.sub(r"(공급규모|총 공급세대수)\s*[:：]?", "", cleaned)
            cleaned = cleaned.strip(" -")
            cleaned = cleaned.strip()
            info["공급규모"] = cleaned

        # --- 시행사 ---
        if not info["시행사"] and ("시행자" in s or "시행사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = re.sub(r"(시행자|시행사)\s*[:：]?", "", cleaned)
            cleaned = cleaned.strip(" -")
            cleaned = cleaned.strip()
            info["시행사"] = cleaned

        # --- 시공사 ---
        if not info["시공사"] and ("시공자" in s or "시공사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = re.sub(r"(시공자|시공사)\s*[:：]?", "", cleaned)
            cleaned = cleaned.strip(" -")
            cleaned = cleaned.strip()
            info["시공사"] = cleaned

    return info


# ============================
#  분석 함수 4: 표에서 시행사 / 시공사 추출
# ============================
def extract_company_info_from_table(pdf):
    """
    '사업주체 / 시공사 / 분양대행사' 같은 표에서
    시행사(사업주체)와 시공사를 찾아내는 함수.
    """
    result = {
        "시행사": None,
        "시공사": None,
    }

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            if not table:
                continue

            # 각 테이블에서 헤더 행(사업주체, 시공사 포함)을 찾는다.
            for r_idx, row in enumerate(table):
                if not row:
                    continue

                header_cells = [str(c or "") for c in row]
                header_join = " ".join(header_cells)

                if ("사업주체" in header_join or "사업 주체" in header_join) and "시공사" in header_join:
                    # 컬럼 인덱스 찾기
                    col_siheng = None
                    col_sigong = None
                    for c_idx, cell in enumerate(header_cells):
                        if "사업주체" in cell or "사업 주체" in cell:
                            col_siheng = c_idx
                        if "시공사" in cell:
                            col_sigong = c_idx

                    if col_siheng is None and col_sigong is None:
                        continue

                    # 헤더 아래 행에서 실제 회사명 추출 (예: 회사명 행)
                    for rr in range(r_idx + 1, len(table)):
                        row2 = table[rr]
                        if not row2:
                            continue

                        # 시행사
                        if col_siheng is not None and col_siheng < len(row2):
                            val = row2[col_siheng]
                            if val and not result["시행사"]:
                                result["시행사"] = str(val).strip()

                        # 시공사
                        if col_sigong is not None and col_sigong < len(row2):
                            val = row2[col_sigong]
                            if val and not result["시공사"]:
                                result["시공사"] = str(val).strip()

                        # 둘 다 찾았으면 종료
                        if result["시행사"] and result["시공사"]:
                            return result

    return result


# ============================
#  분석 함수 5: 표에서 청약 일정 추출
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
                    cell_t = str(cell).replace(" ", "")

                    for key, label in header_map.items():
                        if key.replace(" ", "") in cell_t:

                            for rr in range(r + 1, len(rows)):
                                if c >= len(rows[rr]):
                                    continue

                                raw = rows[rr][c] or ""
                                found = re.findall(date_pattern, str(raw))
                                if not found:
                                    continue

                                if label in ["서류접수", "계약체결"] and len(found) >= 2:
                                    update(label, f"{found[0]} ~ {found[-1]}")
                                else:
                                    update(label, found[0])

                                break

    return schedule


# ============================
# Streamlit UI
# ============================
st.set_page_config(page_title="입주자모집공고 분석기", layout="wide")

st.sidebar.title("📂 PDF 업로드")
uploaded = st.sidebar.file_uploader("PDF 파일을 업로드하세요", type=["pdf"])

st.title("🏢 입주자모집공고 분석기 (자동 분석)")

if uploaded:
    # --- 텍스트 추출 ---
    uploaded.seek(0)
    text = ""
    with pdfplumber.open(uploaded) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"

    # --- 표 기반 분석 (청약일정 + 시행/시공사) ---
    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        company = extract_company_info_from_table(pdf)

    # ---------------------------
    # 결과 표시 (오른쪽 영역)
    # ---------------------------
    st.subheader("🧠 자동 분석 결과")

    st.markdown(f"**🏢 단지명:** {parse_complex_name(text) or '정보 없음'}")
    st.markdown(f"**📍 공급 위치:** {parse_location(text) or '정보 없음'}")

    # 핵심 정보 + 표 기반 회사 정보 병합
    core = extract_core_info(text)

    if company.get("시행사"):
        core["시행사"] = company["시행사"]
    if company.get("시공사"):
        core["시공사"] = company["시공사"]

    st.subheader("📌 핵심 정보 요약")
    for k, v in core.items():
        st.write(f"- **{k}**: {v or '정보 없음'}")

    st.subheader("🗓 청약 일정 자동 분류")

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

    st.table(pd.DataFrame(rows))

else:
    st.info("PDF 파일을 업로드해주세요.")
