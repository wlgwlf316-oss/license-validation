package com.capstone.license_server.controller;

import org.springframework.http.*;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "http://localhost:3000") // 1. 리액트 CORS 에러 방지 문 열기
public class LicenseController {

    @PostMapping("/upload") // 2. 리액트가 파일 올리는 주소 (/api/upload)
    public ResponseEntity<String> uploadFile(@RequestParam("file") MultipartFile file) {
        try {
            // 3. 파이썬 FastAPI 서버 분석 주소 (포트 8000번 엔드포인트)
        	String fastapiUrl = "http://localhost:8000/analyze";

            // 4. 파이썬으로 파일을 토스하기 위한 폼 데이터(Multipart) 생성
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.MULTIPART_FORM_DATA);

            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
            body.add("file", file.getResource());

            HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);

            // 5. RestTemplate을 이용해 파이썬 엔진 호출 및 결과 수신
            RestTemplate restTemplate = new RestTemplate();
            ResponseEntity<String> response = restTemplate.postForEntity(fastapiUrl, requestEntity, String.class);

            // 6. AI 분석 방식 및 라이선스 결과를 리액트로 그대로 반환
            return ResponseEntity.ok(response.getBody());

        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body("백엔드 중계 에러 발생: " + e.getMessage());
        }
    }
}