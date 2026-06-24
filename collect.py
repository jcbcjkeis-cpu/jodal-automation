import requests
import json
from datetime import datetime, timedelta

# =================================================================
# 1. 환경 설정 (인증키 및 웹사이트 창고 주소 세팅)
# =================================================================
DATA_GO_KR_KEY = "f1116ba156f900a07b00c69c8eef2a81f701a8901229d58034d89b3cab48eb81"
AIRTABLE_PAT = "patNFBy9lHvRNwLn7.264dea33a571b1e33f231d3941664e1b5279280dc6074807055cf25280737402"
AIRTABLE_BASE_ID = "app1MjAxujoQbvSmQ"
AIRTABLE_TABLE_ID = "tblvKSL7mIVvZ0CdQ"

DATA_URL = "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListThng"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"

# 최근 7일간의 조회 기간 자동 계산
now = datetime.now()
one_week_ago = now - timedelta(days=7)
inqryBgnDt = one_week_ago.strftime("%Y%m%d0000")
inqryEndDt = now.strftime("%Y%m%d%H%M")

print(f"📅 데이터 조회 기간 세팅: {inqryBgnDt} ~ {inqryEndDt}")

# =================================================================
# 2. 조달청 오픈 API 호출
# =================================================================
print("🔄 조달청 공식 엔드포인트에서 원시 계약 데이터를 당겨오는 중...")

params = {
    'serviceKey': DATA_GO_KR_KEY,
    'type': 'json',
    'inqryDiv': '1',
    'inqryBgnDt': inqryBgnDt,
    'inqryEndDt': inqryEndDt,
    'numOfRows': '50',
    'pageNo': '1'
}

response = requests.get(DATA_URL, params=params)

if response.status_code != 200:
    print(f"❌ 조달청 API 호출 실패 (HTTP 상태 코드: {response.status_code})")
else:
    try:
        data = response.json()
        items = data.get('response', {}).get('body', {}).get('items', [])
        
        if not items:
            print("⚠️ 해당 기간 내에 수집된 데이터가 존재하지 않습니다.")
        else:
            print(f"✅ 조달청 원시 데이터 {len(items)}건 확보 완료. 정제 파이프라인 가동...")
            
            headers = {
                "Authorization": f"Bearer {AIRTABLE_PAT}",
                "Content-Type": "application/json"
            }

            success_count = 0

            for item in items:
                cntrct_method = item.get('cntrctCnclsMthdNm', '')
                if "수의" not in cntrct_method:
                    continue
                    
                # corpList 구조 파싱
                corp_list_str = item.get('corpList', '')
                corp_name = "상호명 미표기"
                biz_no = "000-00-00000"
                
                if corp_list_str:
                    clean_str = str(corp_list_str).strip('[]')
                    parts = clean_str.split('^')
                    if len(parts) >= 4:
                        corp_name = parts[3]
                    if len(parts) >= 10:
                        biz_no = parts[9]
                    elif len(parts) > 4:
                        biz_no = parts[-1]
                        
                try:
                    amt = int(item.get('thtmCntrctAmt', item.get('totCntrctAmt', 0)))
                except:
                    amt = 0

                # [핵심 수정: 날짜 3중 방어막] 
                # 메인날짜가 없거나 빈칸이면 ➔ 서브날짜 확인 ➔ 그것도 없으면 ➔ 오늘날짜 강제 부여
                final_date = item.get('cntrctCnclsDate', '').strip() or item.get('cntrctDate', '').strip() or datetime.today().strftime('%Y-%m-%d')

                fields = {
                    "계약명": item.get('cntrctNm', '무제명 계약'),
                    "업체명": corp_name,
                    "사업자번호": biz_no,
                    "계약금액": amt,
                    "계약일자": final_date,
                    "발주기관": item.get('cntrctInsttNm', '공공기관')
                }
                
                payload = {"fields": fields}
                airtable_response = requests.post(AIRTABLE_URL, headers=headers, json=payload)
                
                if airtable_response.status_code == 200:
                    success_count += 1
                else:
                    print(f"⚠️ 에어테이블 저장 실패 원인: {airtable_response.text}")

            print("-" * 60)
            print(f"🏁 파이프라인 실행 종료!")
            print(f"📦 필터링 및 날짜 예외 처리가 완비된 데이터 총 {success_count}건이 에어테이블에 이식되었습니다.")
            print("-" * 60)

    except json.JSONDecodeError:
        print("❌ 정상 연결되었으나 데이터 구조 변환에 실패했습니다.")
