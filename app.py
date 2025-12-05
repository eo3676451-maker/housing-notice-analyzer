import streamlit as st
import pdfplumber
import re
from datetime import datetime

# ============================
#  공통 유틸
# ============================
def parse_ymd(date_str: str):
    """'YYYY.MM.DD' 문자열을 datetime.date로 변환 (실패 시 None)"""
    try:
        return datetime.strptime(date_str, "%Y.%m.%d").date()
    except Exception:
        return None

# ============================
#  분석 함수 1: 단지명 추출
# ============================
def parse_complex_name(text: str):
    for line in text.splitlines():
        line = line.strip()
        if "입주자모집공고" in line:
            name = line.replace("입주자모집공고", "").strip()
            if name:
                return name
    return None

# ============================
#  분석 함수 2: 공급위치 추출
# ============================
def parse_location(text: str):
    keywords = ["공급위치", "사업위치", "건설위치"]
    for line in text.splitlines():
        line = line.strip()
        for key in keywords:
            if key in line:
                cleaned = line.replace(key, "").replace(":", "").strip()
                return cleaned
    return None

# ============================
#  분석 함수 3: 전체 날짜 리스트 (참고용)
# ============================
def parse_dates(text: str):
    pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"
    dates = re.findall(pattern, text)
    return dates if dates else None

# ============================
#  분석 함수 4: 표(table)에서 청약 일정 추출
# ============================
def extract_schedule_from_table(pdf):
    """
    PDF 안의 '청약 일정 표'에서
    열(column) 기준으로 일정 날짜를 추출하는 버전.
    - 1행(혹은 헤더행)에서 라벨을 찾고
    - 같은 열의 아래 행에서 날짜를 가져온다.
    - 같은 라벨이 여러 번 나오면 가장 최근 날짜(연도가 큰 것)를 사용한다.
    """

    schedule = {
        "입주자모집공고일": None,
        "특별공급 접수일": None,
        "일반공급 1순위 접수일": None,
        "일반공급 2순위 접수일": None,
        "당첨자발표일": None,
        "서류접수": None,
        "계약체결": None,
    }

    # 표 헤더 셀에서 찾을 키워드 ↔ 우리가 쓰는 라벨
    header_map = {
    "입주자모집공고": "입주자모집공고일",
    "입주자 모집공고": "입주자모집공고일",

    # 특별공급 (정확한 열만 잡도록 수정)
    "특별공급접수": "특별공급 접수일",
    "특별공급 신청": "특별공급 접수일",
    "특별공급 접수": "특별공급 접수일",

    "1순위 접수": "일반공급 1순위 접수일",
    "1순위": "일반공급 1순위 접수일",

    "2순위 접수": "일반공급 2순위 접수일",
    "2순위": "일반공급 2순위 접수일",

    "당첨자발표일": "당첨자발표일",
    "당첨자 발표": "당첨자발표일",

    "서류접수": "서류접수",
    "서류 접수": "서류접수",

    "정당계약": "계약체결",
    "계약체결": "계약체결",
    "계약 체결": "계약체결",
}


    date_pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"

    def update_if_later(label: str, new_value: str):
        """같은 라벨이 여러 번 나왔을 때 더 최근(큰 연도) 날짜만 남기기"""
        import re
        from datetime import datetime

        def first_date(s: str):
            m = re.search(date_pattern, s)
            if not m:
                return None
            try:
                return datetime.strptime(m.group(), "%Y.%m.%d").date()
            except Exception:
                return None

        new_d = first_date(new_value)
        if not new_d:
            return

        old_val = schedule.get(label)
        if not old_val:
            schedule[label] = new_value
            return

        old_d = first_date(old_val)
        if not old_d or new_d > old_d:
            schedule[label] = new_value

    # 페이지/테이블 별로 탐색
    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            if not table:
                continue

            rows = table

            # 모든 셀을 순회하면서 '헤더 셀' 위치 찾기
            for r_idx, row in enumerate(rows):
                if not row:
                    continue
                for c_idx, cell in enumerate(row):
                    cell_text = (cell or "").replace(" ", "").strip()
                    if not cell_text:
                        continue

                    for header_sub, label in header_map.items():
                        if header_sub.replace(" ", "") in cell_text:
                            # 같은 열(c_idx)의 아래쪽 행에서 날짜 찾기
                            for rr in range(r_idx + 1, len(rows)):
                                if c_idx >= len(rows[rr]):
                                    continue
                                val_cell = rows[rr][c_idx]
                                val_text = (val_cell or "").strip()
                                if not val_text:
                                    continue

                                dates = re.findall(date_pattern, val_text)
                                if not dates:
                                    continue

                                # 서류/계약은 기간일 수 있음
                                if label in ("서류접수", "계약체결") and len(dates) >= 2:
                                    candidate = f"{dates[0]} ~ {dates[-1]}"
                                else:
                                    candidate = dates[0]

                                update_if_later(label, candidate)
                                break  # 이 헤더에 대해선 더 아래 안 내려가도 됨

    return schedule


# ============================
# Streamlit UI
# ============================
st.set_page_config(page_title="입주자모집공고 분석기 - 자동 분석 버전")

st.title("입주자모집공고 분석기 (자동 분석 버전)")
st.write("업로드된 PDF에서 텍스트를 추출하고, 단지명 · 공급위치 · 전체 날짜 · 청약일정을 자동 분석합니다.")

uploaded_file = st.file_uploader("입주자모집공고 PDF를 선택하세요", type=["pdf"])

if uploaded_file is not None:
    st.success("📁 파일 업로드 완료! 텍스트를 추출하는 중입니다...")

    # 1) PDF 전체 텍스트 추출
    uploaded_file.seek(0)
    full_text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            full_text += f"\n\n========== {page_num} 페이지 ==========\n\n"
            full_text += text

    # 2) 표에서 일정 추출
    uploaded_file.seek(0)  # 파일 포인터 다시 처음으로
    with pdfplumber.open(uploaded_file) as pdf:
        table_schedule = extract_schedule_from_table(pdf)

    # --------------------
    # 원본 텍스트 & 미리보기
    # --------------------
    st.subheader("📌 추출된 전체 텍스트")
    st.text_area("PDF 텍스트", full_text, height=300)

    st.subheader("👀 미리보기 (앞부분 500자)")
    preview = full_text[:500] + "..." if len(full_text) > 500 else full_text
    st.write(preview)

    # --------------------
    # 자동 분석 결과
    # --------------------
    st.subheader("🧠 자동 분석 결과")

    # ① 단지명
    complex_name = parse_complex_name(full_text)
    st.markdown(f"**🏢 단지명:** {complex_name if complex_name else '찾지 못함'}")

    # ② 공급위치
    location = parse_location(full_text)
    st.markdown(f"**📍 공급 위치:** {location if location else '찾지 못함'}")

    # ③ 전체 날짜 목록 (참고용)
    st.subheader("📅 청약 관련 날짜 정보 (전체)")
    dates = parse_dates(full_text)
    if dates:
        unique_dates = sorted(set(dates))
        st.write(f"총 추출된 날짜 개수: {len(dates)}")
        st.write(f"서로 다른 날짜 개수: {len(unique_dates)}")
        for d in unique_dates:
            st.write(f"- {d}")
    else:
        st.warning("날짜 정보를 찾지 못했습니다.")

    # ④ 표 기반 청약 일정 자동 분류
    st.subheader("🗓 청약 일정 자동 분류 (표 기반)")

    label_order = [
        "입주자모집공고일",
        "특별공급 접수일",
        "일반공급 1순위 접수일",
        "일반공급 2순위 접수일",
        "당첨자발표일",
        "서류접수",
        "계약체결",
    ]

    for label in label_order:
        value = table_schedule.get(label)
        st.write(f"- **{label}**: {value if value else '정보 없음'}")

else:
    st.info("PDF 파일을 업로드해주세요.")
