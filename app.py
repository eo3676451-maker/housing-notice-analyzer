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
    for line in text.splitlines():
        if "입주자모집공고" in line:
            cleaned = line.replace("입주자모집공고", "").strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            return cleaned.strip(" ,·-")
    return None


# ============================
#  분석 함수 2: 공급위치 추출
# ============================
def parse_location(text: str):
    keywords = ["공급위치", "사업위치", "건설위치", "대지위치"]
    for line in text.splitlines():
        for key in keywords:
            if key in line:
                cleaned = line.replace(key, "")
                cleaned = cleaned.replace(":", "").replace("■", "")
                cleaned = cleaned.replace("위치", "").strip()
                cleaned = re.sub(r"\s+", " ", cleaned)
                return cleaned
    return None


# ============================
#  분석 함수 3: 핵심 정보 추출 (텍스트 기반)
# ============================
def extract_core_info(text: str):
    info = {"공급규모": None, "시행사": None, "시공사": None}

    for line in text.splitlines():
        s = line.strip()

        # 공급규모
        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            cleaned = s
            cleaned = cleaned.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("공급규모", "").replace("총 공급세대수", "")
            cleaned = cleaned.replace(":", "").strip()
            info["공급규모"] = cleaned

        # 시행사 (너무 긴 문장은 제외)
        if (
            not info["시행사"]
            and ("시행자" in s or "시행사" in s)
            and len(s) <= 60
        ):
            cleaned = s.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시행자", "").replace("시행사", "")
            cleaned = cleaned.replace(":", "").strip()
            info["시행사"] = cleaned

        # 시공사
        if (
            not info["시공사"]
            and ("시공자" in s or "시공사" in s)
            and len(s) <= 60
        ):
            cleaned = s.replace("■", "").replace("●", "")
            cleaned = cleaned.replace("시공자", "").replace("시공사", "")
            cleaned = cleaned.replace(":", "").strip()
            info["시공사"] = cleaned

    return info


# ============================
#  분석 함수 4: 표에서 회사정보 추출
# ============================
def extract_company_from_tables(pdf):
    result = {"시행사": None, "시공사": None}

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            rows = [[(c or "").strip() for c in row] for row in table]

            header_idx = None
            for i, row in enumerate(rows):
                if "사업주체" in " ".join(row) and "시공사" in " ".join(row):
                    header_idx = i
                    break

            if header_idx is None:
                continue

            header = rows[header_idx]

            col = {}
            for idx, h in enumerate(header):
                if "사업주체" in h:
                    col["시행사"] = idx
                if "시공사" in h:
                    col["시공사"] = idx

            for r in range(header_idx + 1, len(rows)):
                row = rows[r]
                if "회사명" in row[0]:
                    if "시행사" in col:
                        result["시행사"] = row[col["시행사"]]
                    if "시공사" in col:
                        result["시공사"] = row[col["시공사"]]
                    return result
    return result


# ============================
#  분석 함수 5: 중도금 대출 이자 조건 추출
# ============================
def extract_loan_condition(text: str):
    text_nospace = text.replace(" ", "")

    if ("중도금대출" in text_nospace or "중도금" in text_nospace):
        if "이자후불제" in text_nospace:
            return "이자후불제"
        if "무이자" in text_nospace:
            return "무이자"
        if "이자선납" in text_nospace:
            return "이자선납제"

    return "표기 없음"


# ============================
#  분석 함수 6: 표에서 청약 일정 추출
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
        "1순위 접수": "일반공급 1순위 접수일",
        "1순위": "일반공급 1순위 접수일",
        "2순위 접수": "일반공급 2순위 접수일",
        "2순위": "일반공급 2순위 접수일",
        "당첨자발표일": "당첨자발표일",
        "당첨자 발표": "당첨자발표일",
        "서류접수": "서류접수",
        "정당계약": "계약체결",
        "계약체결": "계약체결",
    }

    date_pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"

    def update(label, new_val):
        if schedule[label] is None:
            schedule[label] = new_val
            return
        try:
            old_d = datetime.strptime(re.findall(date_pattern, schedule[label])[0], "%Y.%m.%d")
            new_d = datetime.strptime(re.findall(date_pattern, new_val)[0], "%Y.%m.%d")
            if new_d > old_d:
                schedule[label] = new_val
        except:
            pass

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
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
uploaded = st.sidebar.file_uploader("PDF 파일 업로드", type=["pdf"])

st.title("🏢 입주자모집공고 분석기 (자동 분석)")

if uploaded:
    uploaded.seek(0)
    text = ""

    with pdfplumber.open(uploaded) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"

    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        company = extract_company_from_tables(pdf)

    # ---------------------------
    # 자동 분석 결과 표시
    # ---------------------------
    st.subheader("🧠 자동 분석 결과")

    st.markdown(f"**🏢 단지명:** {parse_complex_name(text) or '정보 없음'}")
    st.markdown(f"**📍 공급 위치:** {parse_location(text) or '정보 없음'}")

    # 핵심 정보(텍스트 기반)
    core = extract_core_info(text)

    # 표 기반 데이터가 더 정확 → 덮어쓰기
    if company.get("시행사"):
        core["시행사"] = company["시행사"]
    if company.get("시공사"):
        core["시공사"] = company["시공사"]

    st.subheader("📌 핵심 정보 요약")
    for k, v in core.items():
        st.write(f"- **{k}**: {v or '정보 없음'}")

    # 🔥 중도금 대출 이자 조건 분석 추가
    st.markdown(f"**💰 중도금 대출 조건:** {extract_loan_condition(text)}")

    # ---------------------------
    # 청약 일정 표시
    # ---------------------------
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
