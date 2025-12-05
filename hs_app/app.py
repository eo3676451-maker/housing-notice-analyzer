import streamlit as st
import pdfplumber
import re
from datetime import datetime
import pandas as pd

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
#  핵심 정보(공급규모 + 텍스트 백업용 시행/시공) 추출
# ============================
def extract_core_info(text: str):
    info = {
        "공급규모": None,
        "시행사": None,   # 텍스트에서 찾는 백업용
        "시공사": None,   # 텍스트에서 찾는 백업용
    }

    for line in text.splitlines():
        s = line.strip()

        # 공급규모
        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "")
            cleaned = cleaned.replace("●", "")
            cleaned = cleaned.replace("공급규모", "")
            cleaned = cleaned.replace("총 공급세대수", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()
            info["공급규모"] = cleaned

        # 시행사(텍스트에 노출되는 경우)
        if not info["시행사"] and ("시행자" in s or "시행사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시행자", "").replace("시행사", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()
            info["시행사"] = cleaned

        # 시공사(텍스트에 노출되는 경우)
        if not info["시공사"] and ("시공자" in s or "시공사" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시공자", "").replace("시공사", "")
            cleaned = cleaned.replace(":", "")
            cleaned = cleaned.strip()
            info["시공사"] = cleaned

    return info


# ============================
#  표에서 사업주체 / 시공사 / 분양대행사 추출
# ============================
def extract_company_from_table(pdf):
    """
    '사업주체 및 시공회사' 같은 표에서
    사업주체(=시행사), 시공사, 분양대행사를 가져온다.
    """
    result = {
        "시행사": None,       # 사업주체 또는 시행사
        "시공사": None,
        "분양대행사": None,
    }

    # 표 헤더에서 찾을 후보 문자열
    target_cols = ["사업주체", "시행사", "시공사", "분양대행사", "분양 대행사"]

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            for row in table:
                if not row:
                    continue
                row_clean = [(c or "").strip() for c in row]

                for idx, cell in enumerate(row_clean):
                    cell_no_space = cell.replace(" ", "")
                    for key in target_cols:
                        if key.replace(" ", "") in cell_no_space:
                            # 같은 행, 바로 오른쪽 셀에 값이 있는 경우가 대부분
                            if idx + 1 < len(row_clean):
                                value = row_clean[idx + 1].strip()
                                if value and len(value) > 1:
                                    if "사업주체" in key or "시행" in key:
                                        result["시행사"] = value
                                    elif "시공" in key:
                                        result["시공사"] = value
                                    elif "분양대행" in key:
                                        result["분양대행사"] = value
    return result


# ============================
#  중도금 대출 조건 추출
# ============================
def extract_loan_condition(text: str):
    """
    텍스트에서 '중도금', '대출', '이자후불제', '무이자' 등의 키워드를 보고
    중도금 대출 조건을 간단히 요약.
    """
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

    # 그래도 못 찾았으면, 그래도 힌트용으로 문장 자체를 리턴
    if not condition and joined:
        condition = joined

    return condition


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
# Streamlit UI
# ============================
st.set_page_config(page_title="입주자모집공고 분석기", layout="wide")

st.sidebar.title("📂 PDF 업로드")
uploaded = st.sidebar.file_uploader("PDF 파일을 업로드하세요", type=["pdf"])

st.title("🏢 입주자모집공고 분석기 (자동 분석)")

if uploaded:
    # 1) 전체 텍스트
    uploaded.seek(0)
    text = ""
    with pdfplumber.open(uploaded) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"

    # 2) 표 기반 정보 (청약일정 + 회사정보)
    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        table_company = extract_company_from_table(pdf)

    # 3) 텍스트 기반 핵심정보 + 중도금 조건
    core = extract_core_info(text)
    loan_cond = extract_loan_condition(text)

    # ---------------------------
    # 결과 출력
    # ---------------------------
    st.subheader("🧠 자동 분석 결과")

    st.markdown(f"**🏢 단지명:** {parse_complex_name(text) or '정보 없음'}")
    st.markdown(f"**📍 공급 위치:** {parse_location(text) or '정보 없음'}")

    st.subheader("📌 핵심 정보 요약")

    # 공급규모
    st.write(f"- **공급규모:** {core.get('공급규모') or '정보 없음'}")

    # 시행사: 표(사업주체/시행사) → 텍스트 순
    final_siheng = (
        table_company.get("시행사")
        or core.get("시행사")
    )
    st.write(f"- **시행사:** {final_siheng or '정보 없음'}")

    # 시공사: 표 → 텍스트 순
    final_sigong = table_company.get("시공사") or core.get("시공사")
    st.write(f"- **시공사:** {final_sigong or '정보 없음'}")

    # 분양대행사(있으면)
    if table_company.get("분양대행사"):
        st.write(f"- **분양대행사:** {table_company['분양대행사']}")

    # 중도금 대출 조건
    st.write(f"- **중도금 대출 조건:** {loan_cond or '정보 없음'}")

    # ---------------------------
    # 청약 일정
    # ---------------------------
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

    st.table(pd.DataFrame(rows))

else:
    st.info("PDF 파일을 업로드해주세요.")
