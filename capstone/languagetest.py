# -*- coding: utf-8 -*-
"""
languagetest.py
================
Ollama(LLM) 호출 '직전'까지의 전체 파이프라인을 LLM 없이 검증하는 테스트/개발용 도구.

이 파일의 목적
--------------
1. LLM에 넘길 JSON 구조가 올바르게 만들어지는지 눈으로 확인한다.
2. 언어와 무관하게(파이썬/자바/JS/C/C++) 앞으로 작성할 구조가 전반적으로 작동하는지 확인한다.
3. 각 단계(ZIP 압축해제 → 언어 판별 → 라이브러리 추출 → 라이선스 조회 → JSON 구성)가
   개별적으로도, 전체 파이프라인으로도 작동하는지 확인한다.

중요
----
- 이 파일은 '실제 main.py'가 아니라, 앞으로 main.py로 옮겨갈 다국어 구조의 선(先)구현이자
  자가 검증 도구다.
- Ollama는 호출하지 않는다. LLM에 넣을 payload를 만들어 화면에 출력하는 데서 멈춘다.
- 라이선스 조회는 두 가지 모드가 있다.
    * MOCK 모드(기본): 인터넷 없이 가짜 라이선스 데이터로 구조만 검증. 빠르고 오프라인 가능.
    * REAL 모드: 실제 PyPI에 조회(파이썬 한정). 인터넷 필요. USE_REAL_LOOKUP=True 로 전환.
- 이 파일을 직접 실행하면 내장 샘플들로 전체 파이프라인 자체 점검을 수행한다.
  (별도 테스트 코드는 추후 작성 예정)

실행
----
    python languagetest.py            # 내장 샘플로 전체 자가 점검
"""

import ast
import io
import json
import os
import re
import zipfile
from typing import Optional

# =============================================================================
# 설정
# =============================================================================

# True 로 바꾸면 파이썬 패키지에 한해 실제 PyPI를 조회한다(인터넷 필요, requests 필요).
# 구조만 검증할 때는 False(MOCK) 로 두는 것을 권장한다.
USE_REAL_LOOKUP = False

# ZIP 해제 시 분석에서 제외할 폴더/파일 (venv, node_modules 등 잡음 제거)
IGNORE_DIR_PARTS = {
    "venv", "node_modules", ".git", "__macosx", "build",
    ".gradle", ".idea", "dist", "__pycache__", ".settings",
}
IGNORE_FILE_NAMES = {".ds_store"}

# import 이름 -> 실제 PyPI 패키지명 보정표 (main.py 의 것과 동일 개념)
PACKAGE_ALIASES = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "PIL": "Pillow",
    "dateutil": "python-dateutil",
    "github": "PyGithub",
    "jwt": "PyJWT",
    "git": "GitPython",
}

# 파이썬 표준 라이브러리로 취급해 조회에서 제외할 이름(간단 버전, 필요시 확장)
PY_STDLIB = {
    "os", "sys", "re", "json", "io", "time", "math", "random", "typing",
    "ast", "itertools", "functools", "collections", "datetime", "pathlib",
    "subprocess", "threading", "logging", "unittest", "zipfile", "abc",
}


# =============================================================================
# STEP 1 : ZIP 압축 해제 + 필터링
# =============================================================================

def extract_zip(zip_bytes: bytes) -> list:
    """
    ZIP 바이트를 받아 (파일경로, 내용바이트) 목록을 돌려준다.
    - venv/node_modules 등 잡음 폴더는 제외
    - 디렉터리 엔트리, 숨김 잡음 파일 제외
    - Zip Slip(../ 경로) 방어
    반환: [ {"path": str, "content": bytes}, ... ]
    """
    results = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue

            # 경로를 소문자로 쪼개 잡음 폴더 판정
            lowered_parts = [p.lower() for p in name.replace("\\", "/").split("/")]
            if any(part in IGNORE_DIR_PARTS for part in lowered_parts):
                continue
            base = lowered_parts[-1]
            if base in IGNORE_FILE_NAMES:
                continue

            # Zip Slip 방어: 절대경로/상위경로 탈출 차단
            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm):
                continue

            try:
                content = zf.read(info)
            except Exception:
                continue
            results.append({"path": name, "content": content})
    return results


# =============================================================================
# STEP 2 : 확장자 기반 언어 판별
# =============================================================================

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
}

# 이름 자체로 의미가 있는 특수 파일(의존성 명세 / 라이선스 원문)
SPECIAL_FILES = {
    "pom.xml": "Java-Maven",
    "build.gradle": "Java-Gradle",
    "package.json": "JavaScript-Manifest",
    "requirements.txt": "Python-Manifest",
    "license": "License-Text",
    "license.txt": "License-Text",
    "copying": "License-Text",
}


def detect_language(file_path: str) -> str:
    """파일 경로/확장자로 언어(또는 특수파일 종류)를 판정한다."""
    base = os.path.basename(file_path).lower()
    if base in SPECIAL_FILES:
        return SPECIAL_FILES[base]
    _, ext = os.path.splitext(base)
    return EXTENSION_LANGUAGE_MAP.get(ext, "Unknown")


# =============================================================================
# STEP 3-a : 언어별 라이브러리(의존성) 추출
# =============================================================================

def extract_python(code: str) -> list:
    """파이썬 소스에서 최상위 import 패키지명 추출 (ast, 실패 시 정규식 폴백)."""
    packages = set()
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    packages.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    packages.add(node.module.split(".")[0])
    except SyntaxError:
        pattern = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
        packages.update(pattern.findall(code))
    # 표준 라이브러리 제외
    return sorted(p for p in packages if p not in PY_STDLIB)


def extract_javascript(code: str) -> list:
    """JS 소스에서 import / require 로 불러오는 외부 모듈명 추출(상대경로 제외)."""
    packages = set()
    # import ... from 'pkg'  /  require('pkg')
    patterns = [
        re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", re.DOTALL),
        re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
        re.compile(r"""import\s+['"]([^'"]+)['"]"""),
    ]
    for pat in patterns:
        for m in pat.findall(code):
            if m.startswith("."):        # 상대경로 모듈 제외
                continue
            # scoped 패키지(@scope/name) 는 그대로, 아니면 첫 세그먼트만
            pkg = m if m.startswith("@") else m.split("/")[0]
            packages.add(pkg)
    return sorted(packages)


def extract_c_cpp(code: str) -> list:
    """C/C++ 소스에서 #include <...> 로 부르는 헤더명 추출.
    주의: C/C++은 중앙 저장소가 없어 '라이브러리'라기보다 헤더 수준 추정이다."""
    packages = set()
    pattern = re.compile(r'#\s*include\s*[<"]([^>"]+)[>"]')
    for header in pattern.findall(code):
        packages.add(header)
    return sorted(packages)


def extract_java_pom(xml_text: str) -> list:
    """pom.xml(XML) 에서 <artifactId> 를 뽑아 의존성명으로 사용(간단 파싱)."""
    packages = set()
    for m in re.findall(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", xml_text):
        packages.add(m)
    return sorted(packages)


def extract_manifest_json(text: str) -> list:
    """package.json 의 dependencies/devDependencies 키를 의존성명으로 사용."""
    packages = set()
    try:
        data = json.loads(text)
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                packages.update(data[key].keys())
    except Exception:
        pass
    return sorted(packages)


def extract_requirements(text: str) -> list:
    """requirements.txt 각 줄에서 패키지명만 추출(버전 지정자 제거)."""
    packages = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!~\[ ]", line)[0].strip()
        if name:
            packages.add(name)
    return sorted(packages)


def extract_dependencies(language: str, content: str) -> list:
    """언어(또는 특수파일 종류)에 맞는 추출기를 호출하는 디스패처."""
    if language == "Python":
        return extract_python(content)
    if language == "JavaScript":
        return extract_javascript(content)
    if language in ("C", "C++", "C/C++ Header"):
        return extract_c_cpp(content)
    if language in ("Java-Maven",):
        return extract_java_pom(content)
    if language == "JavaScript-Manifest":
        return extract_manifest_json(content)
    if language == "Python-Manifest":
        return extract_requirements(content)
    if language == "Java":
        # .java 소스만으로는 외부 라이브러리 파악이 사실상 불가.
        # (실제 의존성은 pom.xml / build.gradle 에 있음)
        return []
    # Java-Gradle, License-Text, Unknown 등은 이 단계에서 추출 대상 아님
    return []


# =============================================================================
# STEP 3-b : 라이브러리 -> 라이선스 조회 (MOCK / REAL)
# =============================================================================

# MOCK 모드에서 사용할 가짜 라이선스 사전(구조 검증용). 없는 건 Unknown 처리.
MOCK_LICENSE_DB = {
    # python
    "requests": "Apache-2.0", "fastapi": "MIT", "numpy": "BSD-3-Clause",
    "pandas": "BSD-3-Clause", "flask": "BSD-3-Clause", "django": "BSD-3-Clause",
    "opencv-python": "Apache-2.0", "scikit-learn": "BSD-3-Clause",
    # javascript
    "react": "MIT", "express": "MIT", "lodash": "MIT", "axios": "MIT",
    "left-pad": "WTFPL",
    # java (artifactId)
    "spring-core": "Apache-2.0", "junit": "EPL-1.0", "guava": "Apache-2.0",
    # c/c++ headers (참고용, 대부분 표준/불명)
    "stdio.h": "Unknown", "iostream": "Unknown", "openssl/ssl.h": "Apache-2.0",
}


def lookup_license(language: str, dep_name: str) -> dict:
    """
    의존성 하나의 라이선스 정보를 반환.
    - MOCK 모드: MOCK_LICENSE_DB 참조
    - REAL 모드: 파이썬만 실제 PyPI 조회, 그 외 언어는 MOCK 로 폴백
    반환 형식은 언어와 무관하게 동일(공통 중간 형식).
    """
    result = {
        "package_name": dep_name,
        "license": "Unknown",
        "author": "Unknown",
        "summary": "No description",
        "project_url": "Unknown",
        "source": "mock",
    }

    if USE_REAL_LOOKUP and language in ("Python", "Python-Manifest"):
        real = _real_pypi_lookup(dep_name)
        if real is not None:
            return real
        # 실패하면 아래 MOCK 로 폴백

    # MOCK 조회
    alias = PACKAGE_ALIASES.get(dep_name, dep_name)
    lic = MOCK_LICENSE_DB.get(alias, MOCK_LICENSE_DB.get(dep_name, "Unknown"))
    result["package_name"] = alias
    result["license"] = lic
    return result


def _real_pypi_lookup(package_name: str) -> Optional[dict]:
    """실제 PyPI 조회(파이썬 전용). requests 가 없거나 실패하면 None."""
    try:
        import requests  # 지연 import: MOCK 모드에선 필요 없음
    except ImportError:
        return None

    real_name = PACKAGE_ALIASES.get(package_name, package_name)
    url = f"https://pypi.org/pypi/{real_name}/json"
    out = {
        "package_name": real_name, "license": "Unknown", "author": "Unknown",
        "summary": "No description", "project_url": "Unknown", "source": "pypi",
    }
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            info = resp.json().get("info", {})
            if info.get("license_expression"):
                out["license"] = info["license_expression"]
            elif info.get("license") and info["license"].upper() != "UNKNOWN":
                out["license"] = info["license"][:50]
            else:
                for c in info.get("classifiers", []):
                    if c.startswith("License ::"):
                        out["license"] = c.split("::")[-1].strip()
                        break
            out["author"] = info.get("author") or "Unknown"
            out["summary"] = info.get("summary") or "No description"
            out["project_url"] = info.get("project_url") or info.get("package_url") or "Unknown"
        return out
    except Exception:
        return None


# =============================================================================
# STEP 3-c : 단일 파일 -> LLM 입력용 JSON payload 구성
# =============================================================================

def build_payload_for_file(file_path: str, content_bytes: bytes) -> dict:
    """
    파일 하나에 대해 LLM 에 넘길 payload(dict)를 만든다.
    실제로 Ollama 를 호출하지는 않는다. 이 dict 를 json.dumps 하면 그대로 프롬프트 재료가 된다.
    """
    language = detect_language(file_path)

    # 텍스트 디코딩(실패해도 죽지 않도록)
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = content_bytes.decode("utf-8", errors="replace")

    deps = extract_dependencies(language, text)

    packages = {}
    for dep in deps:
        info = lookup_license(language, dep)
        packages[info["package_name"]] = info

    top_lines = "\n".join(text.split("\n")[:50])

    payload = {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "extension": os.path.splitext(file_path)[1],
        "language": language,
        "top_code_lines": top_lines,
        "packages": packages,
    }
    return payload


# =============================================================================
# 전체 파이프라인 (ZIP -> payload 목록)
# =============================================================================

def run_pipeline(zip_bytes: bytes) -> list:
    """ZIP 바이트 -> 파일별 LLM payload 목록. (LLM 호출 직전까지)"""
    files = extract_zip(zip_bytes)
    payloads = []
    for f in files:
        payloads.append(build_payload_for_file(f["path"], f["content"]))
    return payloads


def load_folder(folder_path: str) -> list:
    """
    폴더를 직접 읽어 (경로, 내용바이트) 목록으로 반환. (ZIP 없이 폴더로 테스트할 때)
    extract_zip 과 동일한 필터링(venv/node_modules 등 제외)을 적용한다.
    반환: [ {"path": str, "content": bytes}, ... ]
    """
    results = []
    for root, dirs, filenames in os.walk(folder_path):
        # 잡음 폴더는 순회 자체에서 제거(성능 + 정확성)
        dirs[:] = [d for d in dirs if d.lower() not in IGNORE_DIR_PARTS]
        for fname in filenames:
            if fname.lower() in IGNORE_FILE_NAMES:
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, folder_path)
            try:
                with open(full, "rb") as fp:
                    content = fp.read()
            except Exception:
                continue
            results.append({"path": rel.replace("\\", "/"), "content": content})
    return results


def run_pipeline_from_folder(folder_path: str) -> list:
    """폴더 경로 -> 파일별 LLM payload 목록. (ZIP 대신 폴더로 테스트)"""
    files = load_folder(folder_path)
    return [build_payload_for_file(f["path"], f["content"]) for f in files]


# =============================================================================
# 통합 입력 처리 + 예외 처리
# =============================================================================

class InputError(Exception):
    """입력이 유효하지 않거나 분석할 내용이 없을 때 발생시키는 예외."""
    pass


# 실제로 '분석 의미가 있는' 언어/파일 종류 (이 목록에 없으면 무의미 입력 취급)
ANALYZABLE_LANGUAGES = {
    "Python", "JavaScript", "Java", "C", "C++", "C/C++ Header",
    "Java-Maven", "Java-Gradle",
    "Python-Manifest", "JavaScript-Manifest",
    "License-Text",
}

# ZIP 으로 인식할 확장자
ZIP_EXTENSIONS = {".zip"}


def load_any(input_path: str) -> list:
    """
    입력 경로가 무엇이든(단일 파일 / ZIP / 폴더) 알아서 처리해
    (경로, 내용바이트) 목록으로 돌려준다.

    예외:
      - 경로가 없으면 InputError
      - ZIP 이 깨졌으면 InputError
      - 읽을 파일이 하나도 없으면 InputError
    """
    # 1) 존재 여부 확인
    if not os.path.exists(input_path):
        raise InputError(f"입력 경로를 찾을 수 없습니다: {input_path}")

    # 2) 폴더인 경우
    if os.path.isdir(input_path):
        files = load_folder(input_path)
        if not files:
            raise InputError(
                f"폴더 안에 분석할 파일이 없습니다(잡음 폴더만 있거나 비어 있음): {input_path}"
            )
        return files

    # 3) 파일인 경우 - 빈 파일 방어
    size = os.path.getsize(input_path)
    if size == 0:
        raise InputError(f"빈 파일입니다(내용 없음): {input_path}")

    ext = os.path.splitext(input_path)[1].lower()

    # 3-a) ZIP 파일
    if ext in ZIP_EXTENSIONS:
        try:
            with open(input_path, "rb") as fp:
                zip_bytes = fp.read()
            files = extract_zip(zip_bytes)
        except zipfile.BadZipFile:
            raise InputError(f"손상되었거나 올바른 ZIP 파일이 아닙니다: {input_path}")
        except Exception as e:
            raise InputError(f"ZIP 처리 중 오류: {input_path} ({e})")
        if not files:
            raise InputError(
                f"ZIP 안에 분석할 파일이 없습니다(잡음 폴더만 있거나 비어 있음): {input_path}"
            )
        return files

    # 3-b) 그 외 단일 파일 (.py, .js, .java, .c, .cpp, pom.xml 등)
    try:
        with open(input_path, "rb") as fp:
            content = fp.read()
    except Exception as e:
        raise InputError(f"파일을 읽을 수 없습니다: {input_path} ({e})")
    return [{"path": os.path.basename(input_path), "content": content}]


def run_pipeline_any(input_path: str) -> list:
    """
    단일 파일 / ZIP / 폴더 무엇이든 받아 payload 목록을 만든다.
    분석 대상(코드/의존성 파일)이 하나도 없으면 InputError 를 던진다.
    """
    files = load_any(input_path)

    payloads = []
    analyzable_count = 0
    for f in files:
        payload = build_payload_for_file(f["path"], f["content"])
        payloads.append(payload)
        if payload["language"] in ANALYZABLE_LANGUAGES:
            analyzable_count += 1

    # 무의미 입력 방어: 예) 의미 없는 .txt 하나만 들어온 경우
    if analyzable_count == 0:
        detected = ", ".join(sorted({p["language"] for p in payloads})) or "없음"
        raise InputError(
            "분석 가능한 소스코드/의존성 파일이 없습니다.\n"
            f"    (감지된 파일 종류: {detected})\n"
            "    지원 대상: Python(.py), JavaScript(.js), Java(.java/pom.xml), "
            "C(.c), C++(.cpp), requirements.txt, package.json, LICENSE 등"
        )

    return payloads


# =============================================================================
# 시각적 출력 헬퍼
# =============================================================================

def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_payload_pretty(payload: dict):
    """payload 를 사람이 보기 좋게 요약 출력 + 실제 JSON 도 함께 출력."""
    print(f"\n  ▶ 파일명   : {payload['file_name']}")
    print(f"    확장자   : {payload['extension']}")
    print(f"    언어     : {payload['language']}")
    print(f"    의존성 수: {len(payload['packages'])}")
    if payload["packages"]:
        for name, info in payload["packages"].items():
            print(f"      - {name:<20} 라이선스: {info['license']:<15} (출처: {info['source']})")
    else:
        print("      (추출된 외부 의존성 없음)")

    # 실제 LLM 에 넘어갈 JSON (packages 부분) 을 그대로 보여줌
    print("\n    [LLM 입력 JSON 미리보기]")
    preview = {
        "file_name": payload["file_name"],
        "extension": payload["extension"],
        "language": payload["language"],
        "packages": payload["packages"],
    }
    text = json.dumps(preview, ensure_ascii=False, indent=2)
    # 들여쓰기 맞춰 출력
    for line in text.split("\n"):
        print("      " + line)


# =============================================================================
# 내장 샘플 (인터넷/실제 ZIP 없이도 자가 점검 가능)
# =============================================================================

SAMPLE_FILES = {
    "project/app.py": (
        "import os\n"
        "import requests\n"
        "import numpy as np\n"
        "from fastapi import FastAPI\n"
        "import cv2\n"
        "print('hello')\n"
    ),
    "project/frontend/index.js": (
        "import React from 'react';\n"
        "import axios from 'axios';\n"
        "const _ = require('lodash');\n"
        "import './local-style.css';\n"   # 상대경로 -> 제외되어야 함
    ),
    "project/backend/pom.xml": (
        "<project>\n"
        "  <dependencies>\n"
        "    <dependency>\n"
        "      <groupId>org.springframework</groupId>\n"
        "      <artifactId>spring-core</artifactId>\n"
        "    </dependency>\n"
        "    <dependency>\n"
        "      <groupId>junit</groupId>\n"
        "      <artifactId>junit</artifactId>\n"
        "    </dependency>\n"
        "  </dependencies>\n"
        "</project>\n"
    ),
    "project/native/main.cpp": (
        "#include <iostream>\n"
        "#include <openssl/ssl.h>\n"
        "int main() { return 0; }\n"
    ),
    "project/native/util.c": (
        "#include <stdio.h>\n"
        "int add(int a, int b){ return a+b; }\n"
    ),
    "project/requirements.txt": (
        "flask==2.0.1\n"
        "pandas>=1.3\n"
        "# comment line\n"
    ),
    "project/LICENSE": (
        "MIT License\n\nCopyright (c) 2026 ...\n"
    ),
    # 아래 두 개는 필터링으로 '제외'되어야 정상
    "project/venv/lib/site.py": "import junk\n",
    "project/node_modules/react/index.js": "module.exports = {}\n",
    "project/__MACOSX/._app.py": "junk",
}


def make_sample_zip() -> bytes:
    """내장 샘플들을 담은 ZIP 바이트를 메모리에서 생성."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in SAMPLE_FILES.items():
            zf.writestr(path, text)
    return buf.getvalue()


# =============================================================================
# 자가 점검 (직접 실행 시)
# =============================================================================

def self_check():
    print_header("STEP 1 점검 : ZIP 압축 해제 + 필터링")
    zip_bytes = make_sample_zip()
    files = extract_zip(zip_bytes)
    print(f"  ZIP 내 전체 엔트리(원본): {len(SAMPLE_FILES)}개")
    print(f"  필터링 후 분석 대상     : {len(files)}개")
    print("  통과한 파일 목록:")
    for f in files:
        print(f"    - {f['path']}")
    # venv / node_modules / __MACOSX 가 걸러졌는지 검증
    survived = {f["path"] for f in files}
    assert not any("venv" in p or "node_modules" in p or "__MACOSX" in p for p in survived), \
        "❌ 잡음 폴더가 필터링되지 않았습니다!"
    print("  ✅ 잡음 폴더(venv/node_modules/__MACOSX) 정상 제외됨")

    print_header("STEP 2 점검 : 확장자 -> 언어 판별")
    for f in files:
        print(f"    {f['path']:<40} -> {detect_language(f['path'])}")

    print_header("STEP 3 점검 : 언어별 추출 + 라이선스 조회 + JSON 구성")
    payloads = [build_payload_for_file(f["path"], f["content"]) for f in files]
    for p in payloads:
        print_payload_pretty(p)

    print_header("개별 단계 스팟 체크 (assert)")
    # JS 상대경로 제외 확인
    js = next(p for p in payloads if p["file_name"] == "index.js")
    assert "react" in js["packages"] and "axios" in js["packages"], "❌ JS 추출 실패"
    assert not any(k.startswith(".") for k in js["packages"]), "❌ JS 상대경로가 안 걸러짐"
    print("  ✅ JavaScript: react/axios 추출 + 상대경로 제외 확인")

    # Python alias 보정 확인 (cv2 -> opencv-python)
    py = next(p for p in payloads if p["file_name"] == "app.py")
    assert "opencv-python" in py["packages"], "❌ cv2->opencv-python 보정 실패"
    assert "os" not in py["packages"], "❌ 표준 라이브러리(os)가 안 걸러짐"
    print("  ✅ Python: cv2->opencv-python 보정 + 표준 라이브러리 제외 확인")

    # Java pom.xml artifactId 추출 확인
    pom = next(p for p in payloads if p["file_name"] == "pom.xml")
    assert "spring-core" in pom["packages"] and "junit" in pom["packages"], "❌ pom.xml 파싱 실패"
    print("  ✅ Java(pom.xml): spring-core/junit 추출 확인")

    # requirements.txt 추출 확인
    req = next(p for p in payloads if p["file_name"] == "requirements.txt")
    assert "flask" in req["packages"] and "pandas" in req["packages"], "❌ requirements 파싱 실패"
    print("  ✅ requirements.txt: flask/pandas 추출(버전 제거) 확인")

    print_header("전체 파이프라인 점검 : run_pipeline()")
    all_payloads = run_pipeline(zip_bytes)
    print(f"  run_pipeline 반환 payload 수: {len(all_payloads)}개")
    print(f"  현재 조회 모드: {'REAL(PyPI)' if USE_REAL_LOOKUP else 'MOCK(오프라인)'}")
    print("\n  ✅ 모든 단계가 오류 없이 통과했습니다.")
    print("  (다음 단계: 이 payload 를 json.dumps 하여 Ollama 프롬프트에 삽입하면 됨)")


def run_report(input_path: str):
    """단일 파일 / ZIP / 폴더 무엇이든 받아 payload 를 시각적으로 출력한다."""
    kind = "폴더" if os.path.isdir(input_path) else "파일"
    print_header(f"입력 테스트 ({kind}) : {input_path}")

    try:
        payloads = run_pipeline_any(input_path)
    except InputError as e:
        # 무의미/잘못된 입력은 여기서 깔끔하게 안내하고 종료
        print(f"  ⚠️  입력 처리 실패\n    {e}")
        print_header("중단됨")
        return
    except Exception as e:
        # 예상치 못한 오류도 프로그램이 죽지 않게 방어
        print(f"  ❌ 예기치 못한 오류: {e}")
        print_header("중단됨")
        return

    analyzable = sum(1 for p in payloads if p["language"] in ANALYZABLE_LANGUAGES)
    skipped = len(payloads) - analyzable
    print(f"  분석 대상 파일 수: {len(payloads)}개 (유의미 {analyzable} / 건너뜀 {skipped})")
    print(f"  조회 모드: {'REAL(PyPI)' if USE_REAL_LOOKUP else 'MOCK(오프라인)'}")
    for p in payloads:
        print_payload_pretty(p)
    print_header("완료")
    print(f"  ✅ {len(payloads)}개 파일의 LLM 입력 payload 생성 완료")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # 인자로 파일/ZIP/폴더 경로를 주면 그것을 테스트
        #   python languagetest.py testlanguage        (폴더)
        #   python languagetest.py app.py              (단일 파일)
        #   python languagetest.py project.zip         (ZIP)
        run_report(sys.argv[1])
    else:
        # 인자가 없으면 내장 샘플로 자가 점검
        self_check()
