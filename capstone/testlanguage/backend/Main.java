// 샘플 Java 소스 파일
// 주의: .java 소스의 import 만으로는 '외부 라이브러리'를 알 수 없다.
// 실제 의존성은 아래 pom.xml 에서 추출해야 한다. (languagetest 에서 .java 는 빈 결과 반환)
package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import java.util.List;
import java.util.ArrayList;

@SpringBootApplication
public class Main {
    public static void main(String[] args) {
        List<String> items = new ArrayList<>();
        items.add("hello");
        SpringApplication.run(Main.class, args);
    }
}
