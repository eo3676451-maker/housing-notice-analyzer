"""
한화 포레나 부산대연 PDF 분석 웹앱 (Streamlit)
"""
import streamlit as st
import pdfplumber
import pandas as pd
import re
import tempfile
from io import BytesIO

# 페이지 설정
st.set_page_config(
    page_title="입주자모집공고 분석기",
    page_icon="🏠",
    layout="wide"
)

# 스타일
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1E88E5;
        margin-bottom: 1rem;
    }
    .info-box {
        background-color: #E3F2FD;
        padding: 1rem;
        border-radius: 8px;
        margin: 0.5rem 0;
    }
    .stDataFrame {
        width: 100%;
    }
</style>
""", unsafe_allow_html=True)

# ============================
#  분석 함수들
# ============================

def parse_complex_name(text: str):
    """단지명 추출"""
    for line in text.splitlines():
        line = line.strip()
        if "입주자모집공고" in line:
            raw = line.replace("입주자모집공고", "").strip()
            name = re.sub(r"\s+", " ", raw).strip(" ,·-")
            return name or None
    return None


def parse_location(text: str):
    """공급위치 추출"""
    keywords = ["공급위치", "사업위치", "건설위치", "대지위치"]
    for line in text.splitlines():
        for key in keywords:
            if key in line:
                cleaned = line.replace(key, "").replace(":", "").replace("■", "").replace("위치", "").strip()
                return re.sub(r"\s+", " ", cleaned)
    return None


def extract_move_in_date(text: str):
    """입주예정일 추출"""
    for line in text.splitlines():
        s = line.strip().replace(" ", "")
        if any(k in s for k in ["입주시기", "입주예정", "입주예정일"]):
            m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", line)
            if m:
                return f"{m.group(1)}년 {m.group(2)}월"
    return None


def extract_companies(text: str):
    """시행사/시공사/분양대행사 추출"""
    companies = {"시행사": None, "시공사": None, "분양대행사": None}
    
    patterns = {
        "시행사": r"(?:사업주체|시행자|시행사)\s*[: ]\s*([^\n]+)",
        "시공사": r"(?:시공자|시공사|시공)\s*[: ]\s*([^\n]+)",
        "분양대행사": r"(?:분양대행사|분양대행)\s*[: ]\s*([^\n]+)"
    }
    
    for role, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            # 회사명 정규화
            if any(k in name for k in ["조합", "건설", "㈜", "(주)", "개발", "공사"]):
                companies[role] = name[:50]  # 너무 긴 경우 자르기
    
    return companies


def extract_schedule(text: str):
    """청약 일정 추출"""
    schedule = []
    
    patterns = [
        (r"입주자\s*모집공고.*?(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})", "입주자모집공고일"),
        (r"특별공급.*?(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})", "특별공급 접수일"),
        (r"1순위.*?(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})", "일반공급 1순위 접수일"),
        (r"2순위.*?(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})", "일반공급 2순위 접수일"),
        (r"당첨자\s*발표.*?(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})", "당첨자 발표일"),
    ]
    
    for pattern, name in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            schedule.append({"일정": name, "날짜": match.group(1)})
    
    return schedule


def extract_price_table(pdf, pages_to_check=None):
    """공급금액표 추출 (강화된 버전)"""
    price_data = []
    
    # 공급금액 관련 페이지 먼저 찾기
    price_pages = []
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if ("공급금액" in text or "분양금액" in text) and ("주택형" in text or "타입" in text or "동" in text):
            price_pages.append(i)
    
    if not price_pages:
        # 키워드로 못 찾으면 앞 20페이지 검색
        price_pages = list(range(min(20, len(pdf.pages))))
    
    for page_idx in price_pages:
        page = pdf.pages[page_idx]
        tables = page.extract_tables()
        
        for table in tables:
            if len(table) < 3:
                continue
            
            # 테이블 전체 텍스트 확인
            all_text = ' '.join(' '.join(str(c) for c in row if c) for row in table)
            
            # 금액표인지 확인 (분양금액, 대지비, 건축비 키워드)
            if not ("분양금액" in all_text or "대지비" in all_text or "공급금액" in all_text):
                continue
            
            # 헤더 행 찾기 (분양금액, 대지비, 건축비 등이 있는 행)
            header_row_idx = None
            for r_idx, row in enumerate(table[:5]):  # 처음 5행에서 헤더 찾기
                row_text = ' '.join(str(c) for c in row if c)
                if "대지비" in row_text or "건축비" in row_text or "분양금액" in row_text:
                    header_row_idx = r_idx
                    break
            
            if header_row_idx is None:
                # 헤더를 못 찾으면 두번째 행을 헤더로 가정
                header_row_idx = 1
            
            # 데이터 행 처리 (헤더 다음 행부터)
            for row in table[header_row_idx + 1:]:
                if len(row) < 6:
                    continue
                
                try:
                    # 금액 값이 있는 셀 찾기 (8자리 이상 숫자, 쉼표 포함 가능)
                    price_values = []
                    price_indices = []
                    
                    for idx, cell in enumerate(row):
                        cell_str = str(cell).replace(',', '').replace(' ', '').strip() if cell else ''
                        # 1억 이상 금액 (9자리 이상)
                        if cell_str.isdigit() and len(cell_str) >= 9 and int(cell_str) >= 100000000:
                            price_values.append(int(cell_str))
                            price_indices.append(idx)
                    
                    if len(price_values) >= 1:
                        # 가장 큰 값을 분양가 합계로 간주
                        total_price = max(price_values)
                        total_idx = price_indices[price_values.index(total_price)]
                        
                        # 동/라인, 층, 세대수 찾기 (금액 앞쪽 컬럼들)
                        dong_line = ""
                        floor = ""
                        units = ""
                        land_price = 0
                        build_price = 0
                        housing_type = ""
                        
                        # 주택형 찾기 (숫자.숫자 패턴)
                        for idx, cell in enumerate(row[:total_idx]):
                            cell_str = str(cell).strip() if cell else ''
                            if re.match(r'\d{2,3}\.\d+', cell_str):
                                housing_type = cell_str
                                break
                            elif re.match(r'^[4-9]\d$', cell_str):  # 59, 84 같은 약식 표기
                                housing_type = cell_str
                        
                        # 층 정보 찾기
                        for idx, cell in enumerate(row):
                            cell_str = str(cell).strip() if cell else ''
                            if '층' in cell_str or re.match(r'^\d{1,2}$', cell_str) or '~' in cell_str:
                                if not cell_str.replace(',', '').isdigit() or len(cell_str) < 5:
                                    floor = cell_str
                                    break
                        
                        # 동/라인 정보 찾기
                        for idx, cell in enumerate(row):
                            cell_str = str(cell).replace('\n', ' ').strip() if cell else ''
                            if '동' in cell_str or '라인' in cell_str:
                                dong_line = cell_str
                                break
                        
                        # 세대수 찾기 (1~3자리 숫자)
                        for idx, cell in enumerate(row):
                            cell_str = str(cell).strip() if cell else ''
                            if cell_str.isdigit() and 1 <= int(cell_str) <= 500:
                                units = cell_str
                                break
                        
                        # 대지비, 건축비 찾기 (합계보다 작은 금액들)
                        other_prices = [p for p in price_values if p < total_price]
                        if len(other_prices) >= 2:
                            other_prices.sort(reverse=True)
                            build_price = other_prices[0]
                            land_price = other_prices[1]
                        elif len(other_prices) == 1:
                            land_price = other_prices[0]
                        
                        price_data.append({
                            "주택형": housing_type if housing_type else "",
                            "동/라인": dong_line,
                            "층": floor,
                            "세대수": units,
                            "대지비": land_price,
                            "건축비": build_price,
                            "분양가 합계": total_price
                        })
                        
                except Exception as e:
                    continue
    
    # 중복 제거 (분양가 합계 기준)
    seen = set()
    unique_data = []
    for item in price_data:
        key = (item.get("층", ""), item.get("분양가 합계", 0))
        if key not in seen:
            seen.add(key)
            unique_data.append(item)
    
    return unique_data




def extract_supply_table(pdf):
    """공급대상 (주택형별 세대수) 추출"""
    supply_data = []
    
    for page_idx in range(min(15, len(pdf.pages))):
        page = pdf.pages[page_idx]
        text = page.extract_text() or ""
        
        if "공급대상" not in text and ("주택형" not in text or "세대" not in text):
            continue
        
        tables = page.extract_tables()
        
        for table in tables:
            if len(table) < 3:
                continue
                
            header = table[0] if table else []
            header_str = ' '.join(str(h) for h in header if h)
            
            if ("주택형" in header_str or "타입" in header_str) and ("세대" in header_str or "공급" in header_str):
                for row in table[2:]:
                    if len(row) >= 10:
                        try:
                            housing_type = str(row[2]).strip() if row[2] else str(row[1]).strip() if row[1] else ""
                            if re.match(r'\d+\.\d+', housing_type):
                                supply_data.append({
                                    "주택형": housing_type,
                                    "전용면적": str(row[4]).strip() if len(row) > 4 and row[4] else "",
                                    "공급면적": str(row[6]).strip() if len(row) > 6 and row[6] else "",
                                    "총세대수": str(row[10]).strip() if len(row) > 10 and row[10] else "",
                                })
                        except:
                            pass
                break  # 첫 번째 적합한 테이블만
        
        if supply_data:
            break
    
    return supply_data


# ============================
#  메인 UI
# ============================

st.markdown('<div class="main-header">🏠 입주자모집공고 PDF 분석기</div>', unsafe_allow_html=True)
st.markdown("PDF 파일을 업로드하면 자동으로 주요 정보를 추출합니다.")

# 파일 업로드
uploaded_file = st.file_uploader("📄 모집공고 PDF 파일을 업로드하세요", type=['pdf'])

if uploaded_file:
    with st.spinner("PDF 분석 중..."):
        # 임시 파일로 저장
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        
        try:
            with pdfplumber.open(tmp_path) as pdf:
                # 전체 텍스트 추출
                full_text = ""
                for page in pdf.pages:
                    full_text += (page.extract_text() or "") + "\n"
                
                # 정보 추출
                complex_name = parse_complex_name(full_text)
                location = parse_location(full_text)
                move_in = extract_move_in_date(full_text)
                companies = extract_companies(full_text)
                schedule = extract_schedule(full_text)
                price_data = extract_price_table(pdf)
                supply_data = extract_supply_table(pdf)
                
                # 세대수 추출
                total_match = re.search(r'총\s*(\d+)\s*세대', full_text.replace(',', ''))
                total_units = total_match.group(1) if total_match else "N/A"
                
                # 결과 표시
                st.success("✅ 분석 완료!")
                
                # 기본 정보
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("📌 기본 정보")
                    st.markdown(f"""
                    | 항목 | 내용 |
                    |------|------|
                    | **단지명** | {complex_name or 'N/A'} |
                    | **공급위치** | {location or 'N/A'} |
                    | **총 세대수** | {total_units}세대 |
                    | **입주예정일** | {move_in or 'N/A'} |
                    """)
                
                with col2:
                    st.subheader("🏢 사업 주체")
                    st.markdown(f"""
                    | 구분 | 회사명 |
                    |------|--------|
                    | **시행사** | {companies.get('시행사') or 'N/A'} |
                    | **시공사** | {companies.get('시공사') or 'N/A'} |
                    | **분양대행사** | {companies.get('분양대행사') or 'N/A'} |
                    """)
                
                # 청약 일정
                if schedule:
                    st.subheader("📅 청약 일정")
                    df_schedule = pd.DataFrame(schedule)
                    st.dataframe(df_schedule, use_container_width=True, hide_index=True)
                
                # 공급대상표
                if supply_data:
                    st.subheader("🏠 주택형별 세대수")
                    df_supply = pd.DataFrame(supply_data)
                    st.dataframe(df_supply, use_container_width=True, hide_index=True)
                
                # 공급금액표
                if price_data:
                    st.subheader("💰 공급금액표")
                    df_price = pd.DataFrame(price_data)
                    
                    # 금액 포맷팅
                    df_price['분양가 합계'] = df_price['분양가 합계'].apply(lambda x: f"{x:,}원")
                    df_price['대지비'] = df_price['대지비'].apply(lambda x: f"{x:,}원" if x > 0 else "")
                    df_price['건축비'] = df_price['건축비'].apply(lambda x: f"{x:,}원" if x > 0 else "")
                    
                    st.dataframe(df_price, use_container_width=True, hide_index=True)
                else:
                    st.info("공급금액표를 추출하지 못했습니다. PDF 구조에 따라 추출이 제한될 수 있습니다.")
                
                # 엑셀 다운로드 버튼
                st.subheader("📥 결과 다운로드")
                
                # 엑셀 파일 생성
                output = BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    # 기본정보
                    basic_info = pd.DataFrame([
                        ["단지명", complex_name or ""],
                        ["공급위치", location or ""],
                        ["총 세대수", f"{total_units}세대"],
                        ["입주예정일", move_in or ""],
                        ["시행사", companies.get('시행사') or ""],
                        ["시공사", companies.get('시공사') or ""],
                        ["분양대행사", companies.get('분양대행사') or ""],
                    ], columns=["항목", "내용"])
                    basic_info.to_excel(writer, sheet_name='기본정보', index=False)
                    
                    # 청약일정
                    if schedule:
                        pd.DataFrame(schedule).to_excel(writer, sheet_name='청약일정', index=False)
                    
                    # 공급대상
                    if supply_data:
                        pd.DataFrame(supply_data).to_excel(writer, sheet_name='주택형별 세대수', index=False)
                    
                    # 공급금액표
                    if price_data:
                        pd.DataFrame(price_data).to_excel(writer, sheet_name='공급금액표', index=False)
                
                output.seek(0)
                
                file_name = f"{complex_name or 'analysis'}_분석결과.xlsx"
                st.download_button(
                    label="📊 엑셀 파일 다운로드",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
        except Exception as e:
            st.error(f"❌ PDF 분석 중 오류가 발생했습니다: {str(e)}")
        
        finally:
            # 임시 파일 삭제
            import os
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

else:
    st.info("👆 PDF 파일을 업로드해주세요!")
    
    # 사용 가이드
    with st.expander("📖 사용 방법"):
        st.markdown("""
        1. **PDF 업로드**: 입주자모집공고 PDF 파일을 업로드합니다
        2. **자동 분석**: 단지정보, 사업주체, 청약일정, 공급금액표를 자동 추출합니다
        3. **결과 확인**: 추출된 정보를 화면에서 확인합니다
        4. **엑셀 다운로드**: 분석 결과를 엑셀 파일로 다운로드할 수 있습니다
        
        **지원 정보:**
        - 단지명 / 공급위치
        - 시행사 / 시공사 / 분양대행사
        - 청약 일정
        - 주택형별 세대수
        - 동/층별 공급금액표
        """)
