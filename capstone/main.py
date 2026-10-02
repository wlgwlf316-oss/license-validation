# -*- coding: utf-8 -*-
"""
main.py - 다국어 오픈소스 라이선스 분석 엔진 (FastAPI)

주요 변경점 (기존 파이썬 전용 버전 대비)
----------------------------------------
1. 입력: 단일 파일 + ZIP 모두 지원 (ZIP 은 내부 파일을 순회하며 개별 분석)
2. 언어: Python / JavaScript / Java / C 지원 (tree-sitter 기반, 미설치 시 폴백)
3. 조회처: PyPI / npm / Maven Central 로 확장, C 는 큐레이션 테이블로 best-effort
4. 프롬프트: 파일 단위로 '해당 파일의 상단 50줄 + 해당 파일의 의존성 JSON' 만 묶어 호출
             (파일 간 정보가 섞이지 않아 LLM 혼동 최소화)
5. 응답: 파일별 결과 목록 + 단일 파일일 때는 기존 키도 유지(test_runner.py 호환)
"""

import io
import json
import os
import re
import zipfile
from typing import Optional

import requests
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# React(3000) / Spring(8080) 에서 직접 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# 설정
# =============================================================================

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3"
OLLAMA_TIMEOUT = 120          # 기존 코드엔 타임아웃이 없어 무한 대기 위험이 있었음

MAX_TOP_LINES = 50            # LLM 에 넘길 코드 상단 줄 수
MAX_PACKAGES_PER_FILE = 40    # 의존성이 비정상적으로 많을 때 프롬프트 폭주 방지
REGISTRY_TIMEOUT = 3

# ZIP 해제 시 제외할 폴더/파일
IGNORE_DIR_PARTS = {
    "venv", "node_modules", ".git", "__macosx", "build", ".gradle",
    ".idea", "dist", "__pycache__", ".settings", "target", "out",
}
IGNORE_FILE_NAMES = {".ds_store"}

# import 이름 -> 실제 PyPI 패키지명 보정표
PACKAGE_ALIASES = {
    "cv2": "opencv-python", "sklearn": "scikit-learn", "yaml": "pyyaml",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "PIL": "Pillow",
    "dateutil": "python-dateutil", "github": "PyGithub", "jwt": "PyJWT",
    "git": "GitPython",
}

# 파이썬 표준 라이브러리 (조회 대상에서 제외)
PY_STDLIB = {
    "os", "sys", "re", "json", "io", "time", "math", "random", "typing",
    "ast", "itertools", "functools", "collections", "datetime", "pathlib",
    "subprocess", "threading", "logging", "unittest", "zipfile", "abc",
    "asyncio", "csv", "hashlib", "shutil", "socket", "string", "traceback",
    "uuid", "warnings", "copy", "enum", "glob", "inspect", "pickle", "struct",
}

# Java 표준 패키지 (외부 의존성이 아님)
JAVA_STDLIB_PREFIXES = ("java.", "javax.", "jdk.", "sun.", "com.sun.")

# Node 내장 모듈 (외부 의존성이 아님)
NODE_BUILTINS = {
    "fs", "path", "http", "https", "os", "url", "util", "crypto", "events",
    "stream", "buffer", "child_process", "net", "zlib", "assert", "querystring",
}

# =============================================================================
# C 언어 대응 : 중앙 저장소가 없으므로 '헤더 -> 라이브러리/라이선스' 큐레이션 테이블
#   - C 는 PyPI/npm 같은 중앙 레지스트리가 없어 자동 조회가 불가능하다.
#   - 대신 실무에서 헤더 이름이 라이브러리와 사실상 1:1 대응되는 관례를 이용한다.
#   - 표준 헤더는 stdlib 로 분류해 의존성에서 제외한다.
#   - 미등록 헤더는 Unknown 으로 남기고 confidence 를 낮게 표기한다.
# =============================================================================

C_STDLIB_HEADERS = {
    "stdio.h", "stdlib.h", "string.h", "math.h", "time.h", "ctype.h",
    "stdint.h", "stddef.h", "stdbool.h", "limits.h", "errno.h", "assert.h",
    "signal.h", "locale.h", "setjmp.h", "stdarg.h", "float.h", "wchar.h",
    "unistd.h", "fcntl.h", "sys/types.h", "sys/stat.h", "sys/socket.h",
    "pthread.h", "dirent.h", "inttypes.h", "iso646.h", "complex.h",
}

C_HEADER_LIBRARY_MAP = {
    # 헤더(접두어)      : (라이브러리명, SPDX 라이선스, 프로젝트 URL)
    "openssl/":  ("OpenSSL",   "Apache-2.0",       "https://www.openssl.org"),
    "zlib.h":    ("zlib",      "Zlib",             "https://zlib.net"),
    "curl/":     ("libcurl",   "curl",             "https://curl.se"),
    "sqlite3.h": ("SQLite",    "blessing",         "https://sqlite.org"),
    "png.h":     ("libpng",    "libpng-2.0",       "http://libpng.org"),
    "jpeglib.h": ("libjpeg",   "IJG",              "https://ijg.org"),
    "json-c/":   ("json-c",    "MIT",              "https://github.com/json-c/json-c"),
    "cjson/":    ("cJSON",     "MIT",              "https://github.com/DaveGamble/cJSON"),
    "glib.h":    ("GLib",      "LGPL-2.1-or-later","https://gitlab.gnome.org/GNOME/glib"),
    "gtk/":      ("GTK",       "LGPL-2.1-or-later","https://www.gtk.org"),
    "SDL2/":     ("SDL2",      "Zlib",             "https://www.libsdl.org"),
    "SDL/":      ("SDL",       "Zlib",             "https://www.libsdl.org"),
    "ncurses.h": ("ncurses",   "X11",              "https://invisible-island.net/ncurses"),
    "curses.h":  ("ncurses",   "X11",              "https://invisible-island.net/ncurses"),
    "libxml/":   ("libxml2",   "MIT",              "http://xmlsoft.org"),
    "mysql.h":   ("MySQL",     "GPL-2.0-only",     "https://www.mysql.com"),
    "pcre.h":    ("PCRE",      "BSD-3-Clause",     "https://www.pcre.org"),
    "ffi.h":     ("libffi",    "MIT",              "https://sourceware.org/libffi"),
    "uv.h":      ("libuv",     "MIT",              "https://libuv.org"),
    "lua.h":     ("Lua",       "MIT",              "https://www.lua.org"),
    "zmq.h":     ("ZeroMQ",    "MPL-2.0",          "https://zeromq.org"),
    "ssl.h":     ("OpenSSL",   "Apache-2.0",       "https://www.openssl.org"),
    "bzlib.h":   ("bzip2",     "bzip2-1.0.6",      "https://sourceware.org/bzip2"),
    "lzma.h":    ("xz/liblzma","0BSD",             "https://tukaani.org/xz"),
    "gmp.h":     ("GMP",       "LGPL-3.0-or-later","https://gmplib.org"),
    "cairo.h":   ("cairo",     "LGPL-2.1-or-later","https://www.cairographics.org"),
    "freetype/": ("FreeType",  "FTL",              "https://freetype.org"),
    "GL/":       ("OpenGL",    "Unknown",          "https://www.opengl.org"),
    "vulkan/":   ("Vulkan",    "Apache-2.0",       "https://www.vulkan.org"),
}

# =============================================================================
# 언어 판별 (확장자 + 특수 파일명)
# =============================================================================

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".java": "Java",
    ".c": "C", ".h": "C",
}

SPECIAL_FILES = {
    "pom.xml": "Java-Maven",
    "build.gradle": "Java-Gradle",
    "package.json": "JavaScript-Manifest",
    "requirements.txt": "Python-Manifest",
    "license": "License-Text",
    "license.txt": "License-Text",
    "license.md": "License-Text",
    "copying": "License-Text",
}

# 실제로 라이선스 판별 의미가 있는 종류만 분석 대상으로 삼는다.
ANALYZABLE_LANGUAGES = {
    "Python", "JavaScript", "Java", "C",
    "Java-Maven", "Java-Gradle",
    "Python-Manifest", "JavaScript-Manifest",
    "License-Text",
}


def detect_language(file_path: str) -> str:
    """파일 경로로 언어(또는 특수 파일 종류)를 판정."""
    base = os.path.basename(file_path).lower()
    if base in SPECIAL_FILES:
        return SPECIAL_FILES[base]
    _, ext = os.path.splitext(base)
    return EXTENSION_LANGUAGE_MAP.get(ext, "Unknown")


# =============================================================================
# tree-sitter 준비 (미설치 시 폴백)
# =============================================================================

_TS_PARSERS = {}
_TS_AVAILABLE = None


def _get_parser(language: str):
    """언어별 tree-sitter 파서를 지연 로딩. 실패하면 None 반환(폴백 유도)."""
    global _TS_AVAILABLE
    if language in _TS_PARSERS:
        return _TS_PARSERS[language]
    try:
        from tree_sitter import Language, Parser
        if language == "Python":
            import tree_sitter_python as ts_mod
        elif language == "JavaScript":
            import tree_sitter_javascript as ts_mod
        elif language == "Java":
            import tree_sitter_java as ts_mod
        elif language == "C":
            import tree_sitter_c as ts_mod
        else:
            return None
        parser = Parser(Language(ts_mod.language()))
        _TS_PARSERS[language] = parser
        _TS_AVAILABLE = True
        return parser
    except Exception:
        _TS_AVAILABLE = False
        _TS_PARSERS[language] = None
        return None


def _walk(node):
    """트리 전체 노드를 순회하는 제너레이터."""
    yield node
    for child in node.children:
        yield from _walk(child)


# =============================================================================
# 언어별 의존성 추출 (tree-sitter)
# =============================================================================

def extract_python(code: str) -> list:
    """
    Python import 추출.
    tree-sitter 우선, 미설치 시 내장 ast, 그마저 실패하면 정규식 폴백.
    ast 와 동일하게: 점 표기는 첫 부분만, as 별칭은 원본명, 상대 임포트는 제외.
    """
    parser = _get_parser("Python")
    packages = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_statement":
                # import a, b.c / import numpy as np
                for child in node.children:
                    if child.type == "dotted_name":
                        packages.add(child.text.decode().split(".")[0])
                    elif child.type == "aliased_import":
                        name = child.child_by_field_name("name")
                        if name is not None:
                            packages.add(name.text.decode().split(".")[0])
            elif node.type == "import_from_statement":
                module = node.child_by_field_name("module_name")
                # relative_import (from . import x) 는 외부 패키지가 아니므로 제외
                if module is not None and module.type == "dotted_name":
                    packages.add(module.text.decode().split(".")[0])
    else:
        # 폴백 1: 내장 ast
        try:
            import ast as _ast
            tree = _ast.parse(code)
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        packages.add(alias.name.split(".")[0])
                elif isinstance(node, _ast.ImportFrom):
                    if node.module and node.level == 0:
                        packages.add(node.module.split(".")[0])
        except SyntaxError:
            # 폴백 2: 정규식
            pattern = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
            packages.update(pattern.findall(code))

    return sorted(p for p in packages if p and p not in PY_STDLIB)


def extract_javascript(code: str) -> list:
    """JS import / require 추출. 상대경로와 Node 내장 모듈은 제외."""
    parser = _get_parser("JavaScript")
    raw = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_statement":
                # import ... from 'pkg'  /  import 'pkg'
                for child in node.children:
                    if child.type == "string":
                        raw.add(child.text.decode().strip("'\"`"))
            elif node.type == "call_expression":
                # require('pkg')
                fn = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                if fn is not None and fn.text.decode() == "require" and args is not None:
                    for a in _walk(args):
                        if a.type == "string_fragment":
                            raw.add(a.text.decode())
    else:
        # 폴백: 정규식
        for pat in (
            re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", re.DOTALL),
            re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
            re.compile(r"""import\s+['"]([^'"]+)['"]"""),
        ):
            raw.update(pat.findall(code))

    packages = set()
    for m in raw:
        if not m or m.startswith("."):          # 상대경로 모듈 제외
            continue
        pkg = m if m.startswith("@") else m.split("/")[0]
        if pkg in NODE_BUILTINS:                # Node 내장 모듈 제외
            continue
        packages.add(pkg)
    return sorted(packages)


def extract_java_source(code: str) -> list:
    """
    .java 소스의 import 추출.
    주의: 여기서 나오는 값은 'org.springframework.boot' 같은 '패키지 경로'이며
          Maven 좌표(groupId:artifactId)가 아니다. 따라서 정확한 라이선스 조회가
          불가능하고, 어디까지나 보조 힌트로만 사용한다.
          실제 의존성은 pom.xml / build.gradle 에서 얻는다.
    """
    parser = _get_parser("Java")
    packages = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "import_declaration":
                for child in node.children:
                    if child.type in ("scoped_identifier", "identifier"):
                        packages.add(child.text.decode())
    else:
        packages.update(re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", code, re.MULTILINE))

    result = set()
    for full in packages:
        if full.startswith(JAVA_STDLIB_PREFIXES):   # 표준 라이브러리 제외
            continue
        # 최상위 3단계까지만 취해 그룹 수준으로 묶음 (org.springframework.boot)
        parts = full.split(".")
        result.add(".".join(parts[:3]) if len(parts) >= 3 else full)
    return sorted(result)


def extract_c(code: str) -> list:
    """C 의 #include 헤더 추출. 표준 헤더는 제외."""
    parser = _get_parser("C")
    headers = set()

    if parser is not None:
        tree = parser.parse(code.encode("utf-8", errors="replace"))
        for node in _walk(tree.root_node):
            if node.type == "preproc_include":
                for child in node.children:
                    if child.type in ("system_lib_string", "string_literal"):
                        headers.add(child.text.decode().strip('<>"'))
    else:
        headers.update(re.findall(r'#\s*include\s*[<"]([^>"]+)[>"]', code))

    # 표준 헤더 제외, 사용자 로컬 헤더("my.h" 형태 중 매핑 없는 것)는 남기되 Unknown 처리
    return sorted(h for h in headers if h and h not in C_STDLIB_HEADERS)


# =============================================================================
# 매니페스트 파서 (의존성의 권위 있는 출처)
# =============================================================================

def extract_pom_xml(xml_text: str) -> list:
    """pom.xml 에서 groupId:artifactId 쌍을 추출. Maven 조회에 필요한 정식 좌표."""
    deps = []
    for block in re.findall(r"<dependency>(.*?)</dependency>", xml_text, re.DOTALL):
        g = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", block)
        a = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", block)
        if g and a:
            deps.append(f"{g.group(1)}:{a.group(1)}")
        elif a:
            deps.append(a.group(1))
    return sorted(set(deps))


def extract_build_gradle(text: str) -> list:
    """build.gradle 의 'group:artifact:version' 문자열에서 group:artifact 추출."""
    deps = set()
    for m in re.findall(r"""['"]([\w.\-]+:[\w.\-]+)(?::[\w.\-]+)?['"]""", text):
        deps.add(m)
    return sorted(deps)


def extract_package_json(text: str) -> list:
    """package.json 의 dependencies 계열 키 추출."""
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
    """requirements.txt 에서 버전 지정자를 제거한 패키지명 추출."""
    packages = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[=<>!~\[; ]", line)[0].strip()
        if name:
            packages.add(name)
    return sorted(packages)


def extract_dependencies(language: str, content: str) -> list:
    """언어별 추출기 디스패처."""
    if language == "Python":
        return extract_python(content)
    if language == "JavaScript":
        return extract_javascript(content)
    if language == "Java":
        return extract_java_source(content)
    if language == "C":
        return extract_c(content)
    if language == "Java-Maven":
        return extract_pom_xml(content)
    if language == "Java-Gradle":
        return extract_build_gradle(content)
    if language == "JavaScript-Manifest":
        return extract_package_json(content)
    if language == "Python-Manifest":
        return extract_requirements(content)
    return []


# =============================================================================
# 레지스트리 조회 (PyPI / npm / Maven Central / C 큐레이션)
#   - 같은 패키지를 여러 파일이 공유하므로 캐시가 성능에 결정적이다.
# =============================================================================

_LICENSE_CACHE = {}


def _blank_info(name: str) -> dict:
    return {
        "package_name": name, "license": "Unknown", "author": "Unknown",
        "summary": "No description", "project_url": "Unknown",
        "registry": "none", "confidence": "low",
    }


def lookup_pypi(package_name: str) -> dict:
    real_name = PACKAGE_ALIASES.get(package_name, package_name)
    out = _blank_info(real_name)
    out["registry"] = "PyPI"
    try:
        r = requests.get(f"https://pypi.org/pypi/{real_name}/json", timeout=REGISTRY_TIMEOUT)
        if r.status_code == 200:
            info = r.json().get("info", {})
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
            if out["license"] != "Unknown":
                out["confidence"] = "high"
    except Exception:
        pass
    return out


def lookup_npm(package_name: str) -> dict:
    out = _blank_info(package_name)
    out["registry"] = "npm"
    try:
        r = requests.get(f"https://registry.npmjs.org/{package_name}", timeout=REGISTRY_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            latest = data.get("dist-tags", {}).get("latest")
            ver = data.get("versions", {}).get(latest, {}) if latest else {}
            lic = ver.get("license") or data.get("license")
            if isinstance(lic, dict):
                lic = lic.get("type")
            if lic:
                out["license"] = str(lic)[:50]
                out["confidence"] = "high"
            out["summary"] = data.get("description") or "No description"
            author = ver.get("author") or data.get("author")
            if isinstance(author, dict):
                author = author.get("name")
            out["author"] = author or "Unknown"
            out["project_url"] = data.get("homepage") or f"https://www.npmjs.com/package/{package_name}"
    except Exception:
        pass
    return out


def lookup_maven(coordinate: str) -> dict:
    """
    Maven Central 조회. 'groupId:artifactId' 형태를 기대한다.
    POM 파일의 <licenses> 를 읽어 라이선스를 얻는다.
    """
    out = _blank_info(coordinate)
    out["registry"] = "Maven Central"
    if ":" not in coordinate:
        # pom.xml 없이 .java import 로만 얻은 값은 정식 좌표가 아니라 조회 불가
        out["summary"] = "Java 패키지 경로만 확인됨 (pom.xml/build.gradle 필요)"
        return out

    group_id, artifact_id = coordinate.split(":", 1)
    try:
        # 최신 버전 조회
        r = requests.get(
            "https://search.maven.org/solrsearch/select",
            params={"q": f'g:"{group_id}" AND a:"{artifact_id}"', "rows": 1, "wt": "json"},
            timeout=REGISTRY_TIMEOUT,
        )
        version = None
        if r.status_code == 200:
            docs = r.json().get("response", {}).get("docs", [])
            if docs:
                version = docs[0].get("latestVersion") or docs[0].get("v")
        if not version:
            return out

        # POM 에서 라이선스 추출
        group_path = group_id.replace(".", "/")
        pom_url = (f"https://repo1.maven.org/maven2/{group_path}/{artifact_id}/"
                   f"{version}/{artifact_id}-{version}.pom")
        pr = requests.get(pom_url, timeout=REGISTRY_TIMEOUT)
        if pr.status_code == 200:
            text = pr.text
            lic = re.search(r"<licenses>.*?<name>\s*(.*?)\s*</name>", text, re.DOTALL)
            if lic:
                out["license"] = lic.group(1)[:50]
                out["confidence"] = "high"
            url = re.search(r"<licenses>.*?<url>\s*(.*?)\s*</url>", text, re.DOTALL)
            if url:
                out["project_url"] = url.group(1)
            desc = re.search(r"<description>\s*(.*?)\s*</description>", text, re.DOTALL)
            if desc:
                out["summary"] = desc.group(1)[:200]
    except Exception:
        pass
    return out


def lookup_c_header(header: str) -> dict:
    """
    C 는 중앙 레지스트리가 없어 큐레이션 테이블로 best-effort 판별.
    매핑에 없으면 Unknown(로컬 헤더이거나 미등록 라이브러리).
    """
    out = _blank_info(header)
    out["registry"] = "curated-table"
    for key, (lib, lic, url) in C_HEADER_LIBRARY_MAP.items():
        if header == key or header.startswith(key):
            out["package_name"] = lib
            out["license"] = lic
            out["project_url"] = url
            out["summary"] = f"C library inferred from header '{header}'"
            out["confidence"] = "medium"     # 헤더 기반 추정이므로 medium
            return out
    out["summary"] = f"미등록 헤더 '{header}' (로컬 헤더이거나 매핑 테이블에 없음)"
    return out


def lookup_license(language: str, dep_name: str) -> dict:
    """언어에 맞는 레지스트리로 라이선스를 조회한다. 결과는 캐시된다."""
    cache_key = f"{language}::{dep_name}"
    if cache_key in _LICENSE_CACHE:
        return _LICENSE_CACHE[cache_key]

    if language in ("Python", "Python-Manifest"):
        info = lookup_pypi(dep_name)
    elif language in ("JavaScript", "JavaScript-Manifest"):
        info = lookup_npm(dep_name)
    elif language in ("Java", "Java-Maven", "Java-Gradle"):
        info = lookup_maven(dep_name)
    elif language == "C":
        info = lookup_c_header(dep_name)
    else:
        info = _blank_info(dep_name)

    _LICENSE_CACHE[cache_key] = info
    return info


# =============================================================================
# 입력 처리 : 단일 파일 / ZIP
# =============================================================================

def is_zip(filename: str, content: bytes) -> bool:
    """확장자와 매직넘버(PK) 둘 다로 ZIP 여부 판정."""
    if filename and filename.lower().endswith(".zip"):
        return True
    return content[:2] == b"PK"


def extract_zip_files(zip_bytes: bytes) -> list:
    """
    ZIP 을 풀어 분석 대상 파일만 추려 반환.
    - 잡음 폴더 제외, Zip Slip 방어
    - 라이선스 판별에 의미 있는 확장자(ANALYZABLE_LANGUAGES)만 통과
    반환: [ {"path": str, "content": bytes}, ... ]
    """
    results = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue

            parts = [p.lower() for p in name.replace("\\", "/").split("/")]
            if any(p in IGNORE_DIR_PARTS for p in parts):
                continue
            if parts[-1] in IGNORE_FILE_NAMES:
                continue

            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm):   # Zip Slip 방어
                continue

            # 유효 확장자만 분석 대상으로 통과
            if detect_language(name) not in ANALYZABLE_LANGUAGES:
                continue

            try:
                results.append({"path": name, "content": zf.read(info)})
            except Exception:
                continue
    return results


def decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


# =============================================================================
# 파일별 분석 재료(payload) 구성
# =============================================================================

def build_file_payload(file_path: str, content: bytes) -> dict:
    """파일 하나에 대한 언어/상단코드/의존성 정보를 모은다. (LLM 호출 전)"""
    language = detect_language(file_path)
    text = decode_text(content)

    deps = extract_dependencies(language, text)
    if len(deps) > MAX_PACKAGES_PER_FILE:
        deps = deps[:MAX_PACKAGES_PER_FILE]     # 프롬프트 폭주 방지

    packages = {}
    for dep in deps:
        info = lookup_license(language, dep)
        packages[info["package_name"]] = info

    return {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "extension": os.path.splitext(file_path)[1],
        "language": language,
        "top_code_lines": "\n".join(text.split("\n")[:MAX_TOP_LINES]),
        "packages": packages,
    }


# =============================================================================
# LLM 프롬프트 (다국어 대응) + 호출
#   설계: 파일 1개당 프롬프트 1개.
#         해당 파일의 상단 50줄과 '그 파일의' 의존성 JSON 만 함께 넣는다.
#         -> 서로 다른 파일의 정보가 섞이지 않아 LLM 혼동이 최소화된다.
#         -> 프롬프트 길이도 파일 단위로 제한되므로 별도 분할이 불필요하다.
# =============================================================================

def build_prompt(payload: dict) -> str:
    language = payload["language"]
    lang_note = ""
    if language == "Java":
        lang_note = ("\n    참고: 이 파일은 Java 소스입니다. Java 는 소스의 import 만으로 외부 "
                     "라이브러리를 특정하기 어렵고, 실제 의존성은 pom.xml / build.gradle 에 "
                     "정의됩니다. 아래 의존성 정보는 보조 참고 자료로만 사용하세요.")
    elif language == "C":
        lang_note = ("\n    참고: 이 파일은 C 소스입니다. C 언어는 중앙 패키지 저장소가 없어 "
                     "아래 라이브러리 정보는 헤더 이름에 근거한 추정입니다. confidence 값을 "
                     "고려하여 신중히 판단하세요.")
    elif language == "License-Text":
        lang_note = ("\n    참고: 이 파일은 라이선스 원문 파일입니다. 본문에 명시된 라이선스를 "
                     "그대로 식별하세요.")

    return f"""
    당신은 엄격한 오픈소스 라이선스 판별 전문가입니다.
    아래는 하나의 소스 파일에 대한 정보입니다. 이 파일 하나만을 대상으로 판별하세요.

    [분석 대상 파일 정보]
    - 파일명   : {payload['file_name']}
    - 파일경로 : {payload['file_path']}
    - 확장자   : {payload['extension']}
    - 언어     : {payload['language']}{lang_note}

    [해당 파일의 코드 상단 {MAX_TOP_LINES}줄]
    ```
{payload['top_code_lines']}
    ```

    [해당 파일이 사용하는 외부 패키지/라이브러리 정보]
    (각 항목의 registry 는 조회한 저장소, confidence 는 신뢰도입니다)
    {json.dumps(payload['packages'], ensure_ascii=False, indent=2)}

    [지시사항]
    1. 위 정보를 바탕으로 '이 소스 파일 자체의 메인 라이선스'를 판별하세요.
       의존 패키지의 라이선스는 판단 근거일 뿐, 그 자체가 이 파일의 라이선스는 아닙니다.
    2. [중요 규칙] 코드 상단에 라이선스가 명시되어 있지 않다면 반드시 'Unknown' 으로
       판별하세요. 'Unlicense' 는 실제로 존재하는 특정 라이선스의 고유 명칭이므로,
       "라이선스가 없다" 는 의미로 절대 'Unlicense' 를 출력하지 마세요.
       (원문에 정확히 'The Unlicense' 라고 적혀 있을 때만 출력)
    3. 결과만 말하지 말고, 제공된 패키지 정보와 코드 내용을 종합해 판별 근거(reasoning)를
       상세히 작성하세요. 의존 패키지 간 라이선스 충돌 위험이 있으면 함께 지적하세요.
    4. [포맷 규칙] "main_license" 값은 반드시 표준 SPDX 식별자 형태로 짧게 답하세요.
       (예: GNU General Public License version 3 -> GPL-3.0,
            Apache License 2.0 -> Apache-2.0, MIT License -> MIT)
    5. 아래 JSON 형식으로만 답하세요. 다른 설명을 덧붙이지 마세요.

    [출력 포맷 (JSON)]
    {{
        "main_license": "판별된 라이선스명 (또는 Unknown)",
        "reasoning": "상세한 분석 근거와 패키지 충돌 위험성 등을 작성"
    }}
    """


def ask_llama3(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    response.raise_for_status()
    return response.json()["response"]


def parse_llm_response(text: str) -> tuple:
    """LLM 응답에서 JSON 부분만 뽑아 (라이선스, 근거) 반환."""
    text = text.strip()
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            cleaned = text.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)
        return (result.get("main_license", "Unknown"),
                result.get("reasoning", "이유를 파싱할 수 없습니다."))
    except Exception as e:
        return "Unknown", f"LLM 응답 분석 실패 (오류: {e})"


def analyze_one_file(payload: dict) -> dict:
    """payload 하나를 LLM 에 보내 판별 결과를 붙여 반환."""
    # 라이선스 원문 파일이나 의존성이 없는 경우에도 LLM 판별은 의미가 있으므로 그대로 진행
    prompt = build_prompt(payload)
    try:
        raw = ask_llama3(prompt)
        print(f"\n==== [AI RAW RESPONSE: {payload['file_name']}] ====\n{raw}\n{'='*40}\n")
        main_license, reason = parse_llm_response(raw)
    except Exception as e:
        print(f"🚨 [오류] {payload['file_name']} 분석 중 예외: {e}")
        main_license, reason = "Unknown", f"LLM 호출 실패 (오류: {e})"

    return {
        "filename": payload["file_name"],
        "file_path": payload["file_path"],
        "extension": payload["extension"],
        "language": payload["language"],
        "detected_packages": payload["packages"],
        "ai_guessed_main_license": main_license,
        "ai_analysis_reason": reason,
    }


# =============================================================================
# 엔드포인트
# =============================================================================

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """
    단일 소스 파일 또는 ZIP 을 받아 라이선스를 분석한다.

    응답 구조
      - results : 파일별 분석 결과 목록 (ZIP 이면 여러 개)
      - summary : 전체 요약
      - 단일 파일인 경우 기존 키(filename/detected_packages/ai_guessed_main_license/
        ai_analysis_reason)도 최상위에 그대로 포함 -> test_runner.py 호환 유지
    """
    content = await file.read()
    filename = file.filename or "unknown"

    # ---- 입력 분기 및 예외 처리 -------------------------------------------
    if not content:
        return {"status": "error", "message": "빈 파일입니다.", "results": []}

    if is_zip(filename, content):
        try:
            targets = extract_zip_files(content)
        except zipfile.BadZipFile:
            return {"status": "error",
                    "message": "손상되었거나 올바른 ZIP 파일이 아닙니다.", "results": []}
        except Exception as e:
            return {"status": "error", "message": f"ZIP 처리 오류: {e}", "results": []}

        if not targets:
            return {"status": "error",
                    "message": ("ZIP 안에 분석 가능한 파일이 없습니다. "
                                "지원 대상: .py, .js, .java, .c, pom.xml, build.gradle, "
                                "package.json, requirements.txt, LICENSE"),
                    "results": []}
    else:
        language = detect_language(filename)
        if language not in ANALYZABLE_LANGUAGES:
            return {"status": "error",
                    "message": (f"지원하지 않는 파일 형식입니다: {filename} "
                                f"(감지된 종류: {language})"),
                    "results": []}
        targets = [{"path": filename, "content": content}]

    # ---- 파일별 분석 (각 파일마다 독립된 프롬프트) --------------------------
    results = []
    for t in targets:
        payload = build_file_payload(t["path"], t["content"])
        results.append(analyze_one_file(payload))

    # ---- 요약 -------------------------------------------------------------
    license_counts = {}
    for r in results:
        lic = r["ai_guessed_main_license"]
        license_counts[lic] = license_counts.get(lic, 0) + 1

    response = {
        "status": "success",
        "input_type": "zip" if len(targets) > 1 or is_zip(filename, content) else "single",
        "results": results,
        "summary": {
            "total_files": len(results),
            "languages": sorted({r["language"] for r in results}),
            "license_distribution": license_counts,
        },
    }

    # ---- 하위 호환 (단일 파일일 때 기존 키 유지) ---------------------------
    if len(results) == 1:
        first = results[0]
        response.update({
            "filename": first["filename"],
            "detected_packages": first["detected_packages"],
            "ai_guessed_main_license": first["ai_guessed_main_license"],
            "ai_analysis_reason": first["ai_analysis_reason"],
        })

    return response


@app.get("/health")
def health():
    """의존성 설치 상태 점검용 (tree-sitter 사용 가능 여부 확인)."""
    _get_parser("Python")
    return {
        "status": "ok",
        "tree_sitter": bool(_TS_AVAILABLE),
        "cached_lookups": len(_LICENSE_CACHE),
    }
