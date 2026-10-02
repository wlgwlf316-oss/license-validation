import os
import json
import requests
import time
import re

API_URL = "http://localhost:8000/analyze" 
SOURCE_DIR = "dataset/source_codes"
LABEL_DIR = "dataset/labels"

def run_automated_test():
    print("🚀 [자동 채점 시작] 디버깅 기능이 강화된 버전으로 검증을 시작합니다...\n")
    
    if not os.path.exists(SOURCE_DIR):
        print(f"❌ 에러: '{SOURCE_DIR}' 폴더가 없습니다 경로를 확인하세요.")
        return
        
    source_files = [f for f in os.listdir(SOURCE_DIR) if f.endswith('.py')]
    source_files.sort()
    
    total_tests = 0
    passed_tests = 0
    failed_cases = []

    for py_file in source_files:
        base_name = py_file.replace('.py', '')
        json_file = f"{base_name}.json"
        
        py_path = os.path.join(SOURCE_DIR, py_file)
        json_path = os.path.join(LABEL_DIR, json_file)
        
        if not os.path.exists(json_path):
            continue
            
        total_tests += 1
        
        with open(json_path, "r", encoding="utf-8") as f:
            ground_truth_data = json.load(f)
            
        expected_license = ground_truth_data.get("ground_truth", "")
            
        try:
            with open(py_path, "rb") as f:
                files = {"file": (py_file, f, "text/x-python")}
                response = requests.post(API_URL, files=files, timeout=60)
                
            response.raise_for_status() 
            result = response.json()
            
            ai_license = result.get("ai_guessed_main_license", "")
            ai_reason = result.get("ai_analysis_reason", "이유 없음")
            
            is_pass = False
            expected_norm = re.sub(r'[^a-zA-Z0-9]', '', expected_license).upper()
            ai_norm = re.sub(r'[^a-zA-Z0-9]', '', ai_license).upper()
            
            if expected_norm and expected_norm in ai_norm:
                is_pass = True
            
            if is_pass:
                print(f"✅ PASS: {py_file} (정답: {expected_license})")
                passed_tests += 1
            else:
                # 💡 [수정] FAIL 발생 시 AI가 판단한 값과 분석 이유를 실시간으로 즉시 출력합니다.
                print(f"❌ FAIL: {py_file} | 정답: [{expected_license}] ➔ AI 판단: [{ai_license}]")
                print(f"   └ 🛠️ AI 분석 근거: {ai_reason}")
                failed_cases.append({
                    "file": py_file,
                    "expected": expected_license,
                    "detected": ai_license 
                })
                
        except Exception as e:
            print(f"💥 통신/시스템 에러 발생 ({py_file}): {e}")
            
        time.sleep(1)

    print("\n" + "="*45)
    print("📊 [최종 검증 결과 리포트]")
    print("="*45)
    print(f"총 테스트 건수: {total_tests}건")
    print(f"성공(PASS): {passed_tests}건")
    print(f"실패(FAIL): {total_tests - passed_tests}건")
    
    if total_tests > 0:
        accuracy = (passed_tests / total_tests) * 100
        print(f"🎯 시스템 정확도: {accuracy:.1f}%")
        
    print("="*45)

if __name__ == "__main__":
    run_automated_test()