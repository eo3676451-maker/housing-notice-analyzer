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
                cleaned = (
                    line.replace(key, "")
                        .replace(":", "")
                        .replace("■", "")
                        .replace("위치", "")
                        .strip()
                )
                cleaned = re.sub(r"\s+", " ", cleaned)
                return cleaned
    return None


# ============================
#  핵심 정보(공급규모 + 텍스트 백업용 시행/시공)
# ============================
def extract_core_info(text: str):
    info = {"공급규모": None, "시행사": None, "시공사": None}

    for line in text.splitlines():
        s = line.strip()

        # 공급규모
        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            cleaned = (s.replace("■", "")
                         .replace("●", "")
                         .replace("공급규모", "")
                         .replace("총 공급세대수", "")
                         .replace(":", "")
                         .strip())
            info["공급규모"] = cleaned

        # 시행사 백업
        if not info["시행사"] and ("시행자" in s or "시행사" in s):
            cleaned = (s.replace("■", "")
                         .replace("●", "")
                         .replace("시행자", "")
                         .replace("시행사", "")
                         .replace(":", "")
                         .strip())
            info["시행사"] = cleaned

        # 시공사 백업
        if not info["시공사"] and ("시공자" in s or "시공사" in s):
            cleaned = (s.replace("■", "")
                         .replace("●", "")
                         .replace("시공자", "")
                         .replace("시공사", "")
                         .replace(":", "")
                         .strip())
            info["시공사"] = cleaned

    return info


# ============================
#  표 기반 회사정보 추출 (사업주체/시행사, 시공사, 분양대행사)
# ============================
def extract_company_from_table(pdf):
    """
    모집공고 표 어디에 있든 ‘시행사 / 사업주체’, ‘시공사’, ‘분양대행사’ 텍스트를 정확히 추출
    """
    result = {
        "시행사": None,
        "시공사": None,
        "분양대행사": None,
    }

    # 매칭 키워드 정의
    key_map = {
        "사업주체": "시행사",
        "시행자": "시행사",
        "시행사": "시행사",
        "시공자": "시공사",
        "시공사": "시공사",
        "분양대행사": "분양대행사",
        "분양 대행사": "분양대행사",
    }

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:

            for row in table:
                if not row:
                    continue

                # 한 줄 전체 문장처럼 생긴 형태 처리
                joined_row = " ".join([(c or "").strip() for c in row])

                # "사업주체 : 00건설" 형태 대응
                for k in key_map.keys():
                    if k in joined_row:
                        after = joined_row.split(k)[-1]
                        after = after.replace(":", "").strip()
                        after = after.split()[0] if after else None

                        target = key_map[k]
                        if after and len(after) > 1:
                            result[target] = after

                # <표 형태> 대응 (왼쪽 항목 / 오른쪽 값)
                clean_row = [(c or "").strip() for c in row]

                for idx, cell in enumerate(clean_row):
                    cell_no_space = cell.replace(" ", "")
                    for key, tgt in key_map.items():
                        if key.replace(" ", "") in cell_no_space:

                            # 오른쪽 셀을 값으로 인식
                            if idx + 1 < len(clean_row):
                                val = clean_row[idx + 1].strip()
                                if val and len(val) > 1:
                                    result[tgt] = val

    return result



# ============================
#  중도금 대출 조건 추출
# ============================
def extract_loan_condition(text: str):
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if ("중도금" in s and ("대출" in s or "이자" in s)):
            lines.append(s)

    joined = " ".join(lines)

    if "이자후불제" in joined or "이자 후불제" in joined:
        return "이자후불제"
    if "무이자" in joined:
        return "무이자"

    return joined if joined else None


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
        "특별공급": "특별공급 접수일",
        "1순위": "일반공급 1순위 접수일",
        "2순위": "일반공급 2순위 접수일",
        "당첨자": "당첨자발표일",
        "서류": "서류접수",
        "정당계약": "계약체결",
        "계약": "계약체결",
    }

    date_pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"

    def update(label, new_value):
        old = schedule.get(label)
        if not old:
            schedule[label] = new_value
            return

        try:
            old_d = datetime.strptime(re.findall(date_pattern, old)[0], "%Y.%m.%d")
            new_d = datetime.strptime(re.findall(date_pattern, new_value)[0], "%Y.%m.%d")
            if new_d > old_d:
                schedule[label] = new_value
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

                                # 기간 형태(예: ~ ) 처리
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
    # ------ 전체 텍스트 추출 ------
    uploaded.seek(0)
    text = ""
    with pdfplumber.open(uploaded) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"

    # ------ 표 기반 정보 ------
    uploaded.seek(0)
    with pdfplumber.open(uploaded) as pdf:
        schedule = extract_schedule_from_table(pdf)
        table_company = extract_company_from_table(pdf)

    # 텍스트 기반 핵심정보 + 중도금
    core = extract_core_info(text)
    loan_cond = extract_loan_condition(text)

    # ---------------- 결과 출력 ----------------
    st.subheader("🧠 자동 분석 결과")

    st.markdown(f"**🏢 단지명:** {parse_complex_name(text) or '정보 없음'}")
    st.markdown(f"**📍 공급 위치:** {parse_location(text) or '정보 없음'}")

    # ---------------- 핵심 정보 ----------------
    st.subheader("📌 핵심 정보 요약")

    st.write(f"- **공급규모:** {core.get('공급규모') or '정보 없음'}")

    final_siheng = table_company.get("시행사") or core.get("시행사")
    st.write(f"- **시행사:** {final_siheng or '정보 없음'}")

    final_sigong = table_company.get("시공사") or core.get("시공사")
    st.write(f"- **시공사:** {final_sigong or '정보 없음'}")

    if table_company.get("분양대행사"):
        st.write(f"- **분양대행사:** {table_company['분양대행사']}")

    st.write(f"- **중도금 대출 조건:** {loan_cond or '정보 없음'}")

    # ---------------- 일정 ----------------
    st.subheader("📅 청약 일정 자동 분류")

    order = [
        "입주자모집공고일", "특별공급 접수일", "일반공급 1순위 접수일",
        "일반공급 2순위 접수일", "당첨자발표일", "서류접수", "계약체결"
    ]

    rows = []
    for key in order:
        val = schedule.get(key)
        rows.append({"항목": key, "일정": val or "정보 없음"})
        st.write(f"- **{key}**: {val or '정보 없음'}")

    st.table(pd.DataFrame(rows))

else:
    st.info("PDF 파일을 업로드해주세요.")
