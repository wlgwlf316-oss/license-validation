// 샘플 C++ 파일 - #include 헤더 추출 테스트용
// 주의: C/C++ 은 중앙 패키지 저장소가 없어 라이선스 자동 판별이 어렵다.
#include <iostream>
#include <vector>
#include <string>
#include <openssl/ssl.h>   // 외부 라이브러리(OpenSSL) 예시

int main() {
    std::vector<std::string> names;
    names.push_back("test");
    std::cout << "hello" << std::endl;
    return 0;
}
