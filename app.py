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
#  표에서 사업주체 / 시공사 / 분양대행사 추출 (개선 버전)
# ============================
def extract_company_from_table(pdf):
    """
    '사업주체 및 시공회사' 표 구조를 이용해서
    - 헤더 행에서 각 컬럼 인덱스를 찾고
    - 그 아래 '회사명' 행(또는 첫 데이터 행)에서 실제 회사명을 읽어온다.
    """
    result = {
        "시행사": None,       # 사업주체 또는 시행사
        "시공사": None,
        "분양대행사": None,
    }

    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            if not table:
                continue

            header_row_idx = None
            col_map = {}  # {"시행사": idx, "시공사": idx, "분양대행사": idx}

            # 1) 헤더 행 찾기 + 각 컬럼 인덱스 기록
            for r, row in enumerate(table):
                cells = [(c or "").strip() for c in row]
                joined = "".join(cells)

                # '사업주체/시행사' 와 '시공사' 가 함께 있는 행이면 헤더 가능성이 큼
                if (("사업주체" in joined or "시행사" in joined) and "시공사" in joined):
                    header_row_idx = r
                    for idx, cell in enumerate(cells):
                        cell_ns = cell.replace(" ", "")
                        if "사업주체" in cell_ns or "시행사" in cell_ns:
                            col_map["시행사"] = idx
                        elif "시공사" in cell_ns or "시공자" in cell_ns:
                            col_map["시공사"] = idx
                        elif "분양대행" in cell_ns:
                            col_map["분양대행사"] = idx
                    break

            if header_row_idx is None or not col_map:
                continue  # 이 테이블은 회사정보 표가 아님

            # 2) 헤더 아래에서 '회사명' 행(또는 첫 데이터 행)을 찾아 회사명 추출
            for r in range(header_row_idx + 1, len(table)):
                row = table[r]
                cells = [(c or "").strip() for c in row]
                if not any(cells):
                    continue

                first_cell = cells[0].replace(" ", "")

                # 회사명 / 상호 같은 라벨이 있으면 가장 우선
                is_company_row = ("회사명" in first_cell or "상호" in first_cell)

                # 혹은 헤더 바로 다음 행(회사명 라벨이 없어도)도 후보로 인정
                if not is_company_row and r == header_row_idx + 1:
                    is_company_row = True

                if not is_company_row:
                    continue

                # 실제 값 읽기
                for key, c_idx in col_map.items():
                    if c_idx < len(cells):
                        val = cells[c_idx].strip()
                        if val:
                            result[key] = val
                break  # 이 표에서 더 이상 찾을 필요 없음

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

    # 그래도 못 찾았으면, 힌트용으로 문장 자체를 리턴
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
    final_siheng = table_company.get("시행사") or core.get("시행사")
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
