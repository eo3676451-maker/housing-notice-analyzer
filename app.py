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
    """'YYYY.MM.DD' -> datetime.date (실패 시 None)"""
    try:
        return datetime.strptime(date_str, "%Y.%m.%d").date()
    except Exception:
        return None


# ============================
#  분석 함수 1: 단지명 추출 + 간단 정리
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

    # 불필요한 공백/문장부호 정리
    name = re.sub(r"\s+", " ", raw)
    name = name.replace("  ", " ").strip(" ,·-")
    return name or None


# ============================
#  분석 함수 2: 공급위치 추출 + 정리
# ============================
def parse_location(text: str):
    keywords = ["공급위치", "사업위치", "건설위치", "대지위치"]
    for line in text.splitlines():
        line = line.strip()
        for key in keywords:
            if key in line:
                cleaned = (
                    line.replace(key, "")
                    .replace(":", "")
                    .replace("위치", "")
                    .strip()
                )
                cleaned = re.sub(r"\s+", " ", cleaned)
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
#  분석 함수 4: 핵심 정보 몇 가지 뽑아보기
#   (공급규모, 시행/시공사 정도만)
# ============================
def extract_core_info(text: str):
    info = {
        "공급규모": None,
        "시행사": None,
        "시공사": None,
    }

    for line in text.splitlines():
        s = line.strip()

        if not info["공급규모"] and ("공급규모" in s or "총 공급세대수" in s):
            info["공급규모"] = s

        if not info["시행사"] and ("시행자" in s or "시행사" in s):
            info["시행사"] = s

        if not info["시공사"] and ("시공자" in s or "시공사" in s):
            info["시공사"] = s

    return info


# ============================
#  분석 함수 5: 표(table)에서 청약 일정 추출 (열 기준)
# ============================
def extract_schedule_from_table(pdf):
    """
    PDF 안의 '청약 일정 표'에서
    열(column) 기준으로 일정 날짜를 추출하는 버전.
    - 헤더 셀(입주자모집공고일 / 특별공급 접수 / 1순위 접수 등)을 찾고
    - 같은 열의 아래 행에서 날짜를 가져온다.
    - 같은 라벨이 여러 번 나와도 가장 최근 날짜만 남긴다.
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

    # 헤더 셀에서 찾을 문자열 → 일정 라벨
    header_map = {
        "입주자모집공고": "입주자모집공고일",
        "입주자 모집공고": "입주자모집공고일",

        # 특별공급 (더 구체적인 헤더만 인식)
        "특별공급접수": "특별공급 접수일",
        "특별공급 신청": "특별공급 접수일",
        "특별공급 접수": "특별공급 접수일",

        "일반공급 1순위 접수": "일반공급 1순위 접수일",
        "1순위 접수": "일반공급 1순위 접수일",
        "1순위": "일반공급 1순위 접수일",

        "일반공급 2순위 접수": "일반공급 2순위 접수일",
        "2순위 접수": "일반공급 2순위 접수일",
        "2순위": "일반공급 2순위 접수일",

        "당첨자발표일": "당첨자발표일",
        "당첨자 발표": "당첨자발표일",
        "당첨자발표": "당첨자발표일",

        "서류접수": "서류접수",
        "서류 접수": "서류접수",

        "정당계약": "계약체결",
        "계약체결": "계약체결",
        "계약 체결": "계약체결",
    }

    date_pattern = r"\d{4}\.\d{1,2}\.\d{1,2}"

    def update_if_later(label: str, new_value: str):
        """같은 라벨이 여러 번 나왔을 때 더 최근 날짜만 남기기"""

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

    # 페이지/테이블 순회
    for page in pdf.pages:
        tables = page.extract_tables() or []
        for table in tables:
            if not table:
                continue

            rows = table

            # 헤더 셀 위치 찾기
            for r_idx, row in enumerate(rows):
                if not row:
                    continue

                for c_idx, cell in enumerate(row):
                    cell_text = (cell or "").replace(" ", "").strip()
                    if not cell_text:
                        continue

                    for header_sub, label in header_map.items():
                        if header_sub.replace(" ", "") not in cell_text:
                            continue

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

                            if label in ("서류접수", "계약체결") and len(dates) >= 2:
                                candidate = f"{dates[0]} ~ {dates[-1]}"
                            else:
                                candidate = dates[0]

                            update_if_later(label, candidate)
                            break  # 이 헤더에 대해서는 더 아래 안 내려가도 됨

    return schedule


# ============================
#  Streamlit UI
# ============================
st.set_page_config(
    page_title="입주자모집공고 분석기",
    layout="wide",
)

st.sidebar.title("📂 PDF 업로드")
uploaded_file = st.sidebar.file_uploader(
    "입주자모집공고 PDF를 선택하세요", type=["pdf"]
)

st.title("🏢 입주자모집공고 분석기 (자동 분석 버전)")
st.write("PDF에서 텍스트·표를 추출해 **단지 정보 / 청약 일정 / 핵심 정보**를 자동으로 보여줍니다.")

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

    # 2) 표에서 청약 일정 추출
    uploaded_file.seek(0)
    with pdfplumber.open(uploaded_file) as pdf:
        table_schedule = extract_schedule_from_table(pdf)

    # --------------------
    # 컬럼 레이아웃
    # --------------------
    col1, col2 = st.columns([2, 1])

    # ===== 좌측: 텍스트 / 미리보기 =====
    with col1:
        st.subheader("📌 추출된 전체 텍스트")
        st.text_area("PDF 텍스트", full_text, height=300)

        st.subheader("👀 미리보기 (앞부분 500자)")
        preview = full_text[:500] + "..." if len(full_text) > 500 else full_text
        st.write(preview)
        # 👉 여기서 '청약 관련 날짜 정보 (전체)' 섹션은 제거됨

    # ===== 우측: 자동 분석 결과 / 일정표 / 다운로드 =====
    with col2:
        st.subheader("🧠 자동 분석 결과")

        # ① 단지명
        complex_name = parse_complex_name(full_text)
        st.markdown(f"**🏢 단지명:** {complex_name if complex_name else '찾지 못함'}")

        # ② 공급위치
        location = parse_location(full_text)
        st.markdown(f"**📍 공급 위치:** {location if location else '찾지 못함'}")

        # ③ 핵심 정보
        core_info = extract_core_info(full_text)
        st.subheader("📌 핵심 정보 요약")
        for k, v in core_info.items():
            st.write(f"- **{k}**: {v if v else '정보 없음'}")

        # ④ 청약 일정 표 기반 정리
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

        schedule_rows = []
        for label in label_order:
            value = table_schedule.get(label)
            schedule_rows.append({"항목": label, "일정": value if value else "정보 없음"})
            st.write(f"- **{label}**: {value if value else '정보 없음'}")

        # ===== 표 형태로 한번 더 보여주기 =====
        st.markdown("---")
        st.markdown("### 📊 청약 일정 표 보기")
        df_schedule = pd.DataFrame(schedule_rows)
        st.table(df_schedule)

        # ===== CSV / Excel 다운로드 버튼 =====
        valid_rows = [r for r in schedule_rows if r["일정"] != "정보 없음"]
        if valid_rows:
            dl_df = pd.DataFrame(valid_rows)

            # CSV
            csv_data = dl_df.to_csv(index=False).encode("utf-8-sig")

            # Excel
            excel_buffer = BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                dl_df.to_excel(writer, index=False, sheet_name="청약일정")
            excel_data = excel_buffer.getvalue()

            st.markdown("### 💾 청약 일정 다운로드")
            st.download_button(
                label="⬇️ CSV로 다운로드",
                data=csv_data,
                file_name="청약일정.csv",
                mime="text/csv",
            )
            st.download_button(
                label="⬇️ Excel로 다운로드",
                data=excel_data,
                file_name="청약일정.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("다운로드할 일정 데이터가 없습니다.")

else:
    st.info("좌측 사이드바에서 PDF 파일을 업로드해주세요.")
