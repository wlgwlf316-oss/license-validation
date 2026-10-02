/* 샘플 C 파일 - #include 헤더 추출 테스트용 */
#include <stdio.h>
#include <stdlib.h>
#include <curl/curl.h>   /* 외부 라이브러리(libcurl) 예시 */

int add(int a, int b) {
    return a + b;
}

int main() {
    printf("sum = %d\n", add(2, 3));
    return 0;
}
