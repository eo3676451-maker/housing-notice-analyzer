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
    """시행사/시공사/분양대행사 추출 (강화 버전)"""
    companies = {"시행사": None, "시공사": None, "분양대행사": None}
    
    # 회사명 키워드
    company_keywords = ["조합", "건설", "㈜", "(주)", "개발", "공사", "기업", "주식회사", "디앤씨", "디엔씨"]
    
    # 텍스트 정규화
    norm_text = text.replace("：", ":").replace("\n", " ")
    
    # 1차: 패턴 매칭
    patterns = {
        "시행사": [
            r"사업주체\s*[:\s]\s*([^\n,]+)",
            r"시행자\s*[:\s]\s*([^\n,]+)",
            r"시행사\s*[:\s]\s*([^\n,]+)",
            r"사업시행자\s*[:\s]\s*([^\n,]+)",
        ],
        "시공사": [
            r"시공사\s*[:\s]\s*([^\n,]+)",
            r"시공자\s*[:\s]\s*([^\n,]+)",
            r"시공\s*[:\s]\s*([^\n,]+(?:건설|공사|기업)[^\n,]*)",
        ],
        "분양대행사": [
            r"분양대행사\s*[:\s]\s*([^\n,]+)",
            r"분양대행\s*[:\s]\s*([^\n,]+)",
        ]
    }
    
    for role, pats in patterns.items():
        for pattern in pats:
            match = re.search(pattern, norm_text)
            if match:
                name = match.group(1).strip()
                # 불필요한 텍스트 제거
                name = re.sub(r'\s+', ' ', name)
                name = name.split('※')[0].strip()
                name = name.split('(단')[0].strip()
                name = name.split('법인')[0].strip() if '법인' in name and len(name) > 20 else name
                
                if any(k in name for k in company_keywords) and len(name) <= 50:
                    companies[role] = name
                    break
    
    return companies


def extract_companies_from_table(pdf):
    """PDF 테이블에서 회사 정보 추출 (강화된 버전)"""
    companies = {"시행사": None, "시공사": None, "분양대행사": None}
    
    # 회사명으로 인정되는 키워드
    company_keywords = ["조합", "건설", "㈜", "(주)", "개발", "공사", "기업", "주식회사", "디앤씨", "태영", "한화", "현대", "GS", "롯데", "대우", "포스코", "SK", "에스알"]
    
    # 앞/뒤 페이지 모두 검색
    for page_idx in range(len(pdf.pages)):
        page = pdf.pages[page_idx]
        text = page.extract_text() or ""
        
        # 사업주체/시공 키워드가 있는 페이지에서만 분석
        if not ("사업주체" in text or "시공사" in text or "시공자" in text):
            continue
        
        tables = page.extract_tables() or []
        
        for table in tables:
            if not table or len(table) < 2:
                continue
            
            all_text = ' '.join(' '.join(str(c) for c in row if c) for row in table)
            
            # 시공/사업주체 키워드가 있는 테이블
            if "시공" not in all_text and "사업주체" not in all_text:
                continue
            
            # 방법 1: 세로 형태 (헤더 행에 사업주체/시공사, 다음 행에 회사명)
            # 예: 행0 = [구분, 사업주체, 시공사, 분양대행사]
            #     행1 = [상호, 조합이름, 건설회사, 분양대행]
            header_row = table[0]
            header_map = {}
            
            for c_idx, cell in enumerate(header_row):
                cell_text = str(cell).replace('\n', ' ').replace(' ', '') if cell else ''
                if '사업주체' in cell_text or '시행' in cell_text:
                    header_map['시행사'] = c_idx
                elif '시공사' in cell_text or '시공자' in cell_text:
                    header_map['시공사'] = c_idx
                elif '분양대행' in cell_text:
                    header_map['분양대행사'] = c_idx
            
            # 헤더를 찾았으면 상호 행에서 회사명 추출
            if header_map:
                for row in table[1:]:
                    if not row:
                        continue
                    row_label = str(row[0]).replace('\n', ' ').replace(' ', '') if row and row[0] else ''
                    
                    # "상호" 또는 "회사명" 행에서 추출
                    if '상호' in row_label or '회사명' in row_label or '회사' in row_label:
                        for role, col_idx in header_map.items():
                            if col_idx < len(row) and row[col_idx]:
                                name = str(row[col_idx]).replace('\n', ' ').strip()
                                if any(k in name for k in company_keywords) and companies[role] is None:
                                    companies[role] = name[:50]
            
            # 방법 2: 가로 형태 (키-값이 같은 행에 있음)
            for row in table:
                if not row or len(row) < 2:
                    continue
                
                for c_idx, cell in enumerate(row):
                    if not cell:
                        continue
                    
                    cell_text = str(cell).replace('\n', ' ').replace(' ', '')
                    
                    # 다음 셀에서 회사명 찾기
                    if c_idx + 1 < len(row) and row[c_idx + 1]:
                        next_cell = str(row[c_idx + 1]).replace('\n', ' ').strip()
                        
                        if any(k in next_cell for k in company_keywords):
                            if '시공사' in cell_text or '시공자' in cell_text:
                                if companies["시공사"] is None:
                                    companies["시공사"] = next_cell[:50]
                            elif '사업주체' in cell_text or '시행' in cell_text:
                                if companies["시행사"] is None:
                                    companies["시행사"] = next_cell[:50]
                            elif '분양대행' in cell_text:
                                if companies["분양대행사"] is None:
                                    companies["분양대행사"] = next_cell[:50]
        
        # 시공사와 시행사 모두 찾으면 종료
        if companies["시공사"] and companies["시행사"]:
            break
    
    return companies


def extract_scale(text: str):
    """공급규모 전체 텍스트 추출"""
    
    # "공급규모" 키워드가 있는 라인 전체 추출
    for line in text.splitlines():
        line = line.strip()
        if "공급규모" in line:
            # ■ 공급규모 : 다음 내용 추출
            cleaned = line.replace("■", "").replace("●", "").strip()
            cleaned = cleaned.replace("공급규모", "").replace(":", "").strip()
            if cleaned and len(cleaned) > 10:
                return cleaned
    
    # 대체 패턴: 지하/지상/동 정보 조합
    scale_parts = []
    
    floor_match = re.search(r'지하\s*(\d+)\s*층[^\d]*지상[^\d]*최고?\s*(\d+)\s*층', text)
    if floor_match:
        scale_parts.append(f"지하 {floor_match.group(1)}층, 지상 최고 {floor_match.group(2)}층")
    
    dong_match = re.search(r'(\d+)\s*개?\s*동', text)
    if dong_match:
        scale_parts.append(f"{dong_match.group(1)}개동")
    
    total_match = re.search(r'총\s*(\d+)\s*세대', text.replace(',', ''))
    if total_match:
        scale_parts.append(f"총 {total_match.group(1)}세대")
    
    return ', '.join(scale_parts) if scale_parts else None


def extract_schedule_from_table(pdf):
    """PDF 테이블에서 청약 일정 추출 (강화 버전)"""
    schedule = {}
    
    # 일정 키워드 매핑
    keyword_map = {
        "입주자모집공고": "입주자모집공고일",
        "모집공고일": "입주자모집공고일",
        "특별공급": "특별공급 접수일",
        "특별공급접수": "특별공급 접수일",
        "1순위": "일반공급 1순위 접수일",
        "일반공급1순위": "일반공급 1순위 접수일",
        "2순위": "일반공급 2순위 접수일",
        "일반공급2순위": "일반공급 2순위 접수일",
        "당첨자발표": "당첨자 발표일",
        "당첨자 발표": "당첨자 발표일",
        "서류접수": "서류접수",
        "계약체결": "계약체결",
        "정당계약": "계약체결",
    }
    
    date_pattern = r'(\d{4}[.]\d{1,2}[.]\d{1,2})'
    
    for page_idx, page in enumerate(pdf.pages[:15]):  # 앞 15페이지만
        text = page.extract_text() or ""
        
        # 일정 관련 키워드가 있는 페이지에서만 분석
        if not ("공고" in text or "접수" in text or "당첨" in text or "청약일정" in text):
            continue
        
        tables = page.extract_tables() or []
        
        for table in tables:
            if not table or len(table) < 2:
                continue
            
            # 행 단위로 분석 (가로 형태 테이블)
            for row in table:
                if not row:
                    continue
                
                for c_idx, cell in enumerate(row):
                    if not cell:
                        continue
                    
                    cell_clean = str(cell).replace(' ', '').replace('\n', '')
                    
                    for keyword, label in keyword_map.items():
                        if keyword.replace(' ', '') in cell_clean:
                            # 같은 행에서 날짜 찾기
                            for other_cell in row[c_idx+1:]:
                                if other_cell:
                                    date_match = re.search(date_pattern, str(other_cell))
                                    if date_match:
                                        # 년도가 2024~2027 범위인지 확인
                                        date_str = date_match.group(1)
                                        year = int(date_str.split('.')[0])
                                        if 2024 <= year <= 2027:
                                            if label not in schedule:
                                                schedule[label] = date_str
                                        break
                            
                            # 다음 행에서 날짜 찾기 (세로 형태)
                            row_idx = table.index(row)
                            if row_idx + 1 < len(table):
                                next_row = table[row_idx + 1]
                                if c_idx < len(next_row) and next_row[c_idx]:
                                    date_match = re.search(date_pattern, str(next_row[c_idx]))
                                    if date_match:
                                        date_str = date_match.group(1)
                                        year = int(date_str.split('.')[0])
                                        if 2024 <= year <= 2027:
                                            if label not in schedule:
                                                schedule[label] = date_str
    
    # 결과를 리스트로 변환 (순서 유지)
    result = []
    order = ["입주자모집공고일", "특별공급 접수일", "일반공급 1순위 접수일", 
             "일반공급 2순위 접수일", "당첨자 발표일", "서류접수", "계약체결"]
    
    for label in order:
        if label in schedule:
            result.append({"일정": label, "날짜": schedule[label]})
    
    return result


def extract_price_table(pdf, pages_to_check=None):
    """공급금액표 추출 (강화된 버전)"""
    price_data = []
    
    # 공급금액 관련 페이지 먼저 찾기
    price_pages = set()
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if ("공급금액" in text or "분양금액" in text) and ("주택형" in text or "타입" in text or "동" in text):
            price_pages.add(i)
            # 다음 페이지도 추가 (연속 페이지 지원)
            if i + 1 < len(pdf.pages):
                price_pages.add(i + 1)
            # 이전 페이지도 추가 (혹시 헤더가 이전 페이지에 있는 경우)
            if i > 0:
                price_pages.add(i - 1)
    
    if not price_pages:
        # 키워드로 못 찾으면 앞 20페이지 검색
        price_pages = set(range(min(20, len(pdf.pages))))
    
    # 정렬해서 순서대로 처리
    for page_idx in sorted(price_pages):
        page = pdf.pages[page_idx]
        tables = page.extract_tables()
        
        for table in tables:
            if len(table) < 3:
                continue
            
            # 테이블 전체 텍스트 확인
            all_text = ' '.join(' '.join(str(c) for c in row if c) for row in table)
            
            # 금액표인지 확인 (분양금액, 대지비, 건축비 키워드 또는 큰 숫자가 있는 경우)
            has_price_keyword = ("분양금액" in all_text or "대지비" in all_text or "공급금액" in all_text)
            
            # 1억 이상 숫자가 여러 개 있으면 금액표로 간주
            big_numbers = re.findall(r'\d{9,}', all_text.replace(',', ''))
            has_big_numbers = len(big_numbers) >= 2
            
            if not has_price_keyword and not has_big_numbers:
                continue
            
            # 헤더 행 찾기 (분양금액, 대지비, 건축비 등이 있는 행)
            header_row_idx = None
            for r_idx, row in enumerate(table[:5]):  # 처음 5행에서 헤더 찾기
                row_text = ' '.join(str(c) for c in row if c)
                if "대지비" in row_text or "건축비" in row_text or "분양금액" in row_text:
                    header_row_idx = r_idx
                    break
            
            # 헤더 없으면 첫 행부터 처리 (연속 페이지 지원)
            # 헤더에서 컬럼 위치 동적 감지
            col_map = {"대지비": -1, "건축비": -1, "합계": -1, "층": -1, "세대수": -1, "동": -1, "주택형": -1, "타입": -1}
            
            if header_row_idx is not None:
                # 헤더가 여러 줄일 수 있으므로 여러 행 검사 (0~2번째 행)
                for h_idx in range(min(3, len(table))):
                    header_row = table[h_idx]
                    for c_idx, cell in enumerate(header_row):
                        cell_text = str(cell).replace(' ', '').replace('\n', '') if cell else ''
                        if '대지비' in cell_text and col_map["대지비"] == -1:
                            col_map["대지비"] = c_idx
                        elif '건축비' in cell_text and col_map["건축비"] == -1:
                            col_map["건축비"] = c_idx
                        elif ('계' == cell_text or '분양금액' in cell_text) and col_map["합계"] == -1:
                            col_map["합계"] = c_idx
                        elif '층' in cell_text and '세대' not in cell_text and col_map["층"] == -1:
                            col_map["층"] = c_idx
                        elif '세대' in cell_text and col_map["세대수"] == -1:
                            col_map["세대수"] = c_idx
                        elif ('동' in cell_text or '라인' in cell_text or '동호' in cell_text) and col_map["동"] == -1:
                            col_map["동"] = c_idx
                        elif '타입' in cell_text and col_map["타입"] == -1:
                            col_map["타입"] = c_idx
                        elif ('주택형' in cell_text or '약식' in cell_text) and col_map["주택형"] == -1:
                            col_map["주택형"] = c_idx
            
            # 헤더가 없거나 핵심 컬럼 못 찾으면 기본값 사용
            if col_map["합계"] == -1:
                # 컬럼 수에 따라 기본 매핑
                num_cols = len(table[0]) if table else 0
                if num_cols >= 15:
                    # 16+ 컬럼: 동래 푸르지오 (16), 서면 어반센트 (19)
                    col_map = {"주택형": 0, "타입": -1, "동": 1, "층": 2, "세대수": 3, "대지비": 4, "건축비": 5, "합계": 6}
                else:
                    # 11 컬럼: 한화 포레나
                    col_map = {"주택형": 0, "타입": -1, "동": 2, "층": 3, "세대수": 4, "대지비": 5, "건축비": 6, "합계": 7}
            
            start_row = header_row_idx + 1 if header_row_idx is not None else 0
            
            # 데이터 행 처리
            for row in table[start_row:]:
                if len(row) < 7:
                    continue
                
                try:
                    # 합계 컬럼에서 분양금액 추출
                    total_idx = col_map["합계"] if col_map["합계"] >= 0 else 7
                    if total_idx >= len(row):
                        continue
                    
                    total_str = str(row[total_idx]).replace(',', '').replace(' ', '').strip() if row[total_idx] else ''
                    
                    if not total_str.isdigit() or int(total_str) < 100000000:
                        continue
                    
                    total_price = int(total_str)
                    
                    # 주택형 추출 (타입 컬럼 우선, 없으면 주택형 컬럼)
                    housing_type = ""
                    if col_map["타입"] >= 0 and col_map["타입"] < len(row) and row[col_map["타입"]]:
                        housing_type = str(row[col_map["타입"]]).strip()
                    if not housing_type and col_map["주택형"] >= 0 and col_map["주택형"] < len(row) and row[col_map["주택형"]]:
                        housing_type = str(row[col_map["주택형"]]).strip()
                    
                    # 동/라인
                    dong_idx = col_map["동"] if col_map["동"] >= 0 else 2
                    dong_line = str(row[dong_idx]).replace('\n', ' ').strip() if dong_idx < len(row) and row[dong_idx] else ""
                    
                    # 층
                    floor_idx = col_map["층"] if col_map["층"] >= 0 else 3
                    floor = str(row[floor_idx]).strip() if floor_idx < len(row) and row[floor_idx] else ""
                    
                    # 세대수
                    units_idx = col_map["세대수"] if col_map["세대수"] >= 0 else 4
                    units = str(row[units_idx]).strip() if units_idx < len(row) and row[units_idx] else ""
                    
                    price_data.append({
                        "주택형": housing_type,
                        "동/라인": dong_line,
                        "층": floor,
                        "세대수": units,
                        "분양가": total_price
                    })
                    
                except Exception as e:
                    continue
    
    # 중복 제거 제거 - 원본 순서 그대로 모든 행 유지
    # (같은 층/금액이지만 다른 동에 속한 행들이 있을 수 있음)
    return price_data




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
                scale = extract_scale(full_text)  # 규모 정보 추가
                
                # 회사 정보 - 텍스트 + 테이블에서 추출
                companies = extract_companies(full_text)
                table_companies = extract_companies_from_table(pdf)
                # 테이블에서 추출한 정보로 보완
                for role in ["시행사", "시공사", "분양대행사"]:
                    if not companies.get(role) and table_companies.get(role):
                        companies[role] = table_companies[role]
                
                # 청약 일정 - 테이블에서 추출
                schedule = extract_schedule_from_table(pdf)
                
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
                    | **규모** | {scale or 'N/A'} |
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
                    df_price['분양가'] = df_price['분양가'].apply(lambda x: f"{x:,}원")
                    
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
                        ["규모", scale or ""],
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
