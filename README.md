# Python으로 실험하는 L4와 L7 로드 밸런싱

Kubernetes나 Docker 없이 Python 표준 라이브러리만으로 L4 TCP passthrough와 L7 HTTPS 경로 라우팅의 차이를 확인합니다. 테스트 인증서 생성을 위해 OpenSSL이 필요합니다.

## 실험 구조

### L4 — TCP passthrough

```text
Client (HTTPS)
     |
     v
L4 Proxy (:8443)
     |  암호화된 TCP 데이터를 그대로 전달
     v
TLS Backend (:9001)
     |  TLS 복호화
     v
HTTP 요청 처리
```

L4 프록시는 TCP 연결만 중계하므로 TLS로 암호화된 HTTP 경로나 헤더를 읽지 않습니다. 따라서 `/users`, `/orders` 모두 동일한 TLS 백엔드로 전달됩니다.

### L7 — TLS 종료 + URL 경로 라우팅

```text
Client (HTTPS)
     |
     v
L7 Proxy (:9443)
     |  TLS 복호화 후 HTTP 경로 확인
     +-- /users  --> User Backend (:9002)
     +-- /orders --> Order Backend (:9003)
```

L7 프록시가 TLS를 종료하고 HTTP 요청 경로를 확인해 목적지를 선택합니다. 프록시에서 백엔드까지는 이 실습 구성상 평문 HTTP를 사용합니다.

## 실행

```bash
python3 l4_l7_demo.py
```

첫 실행 시 스크립트가 OpenSSL로 `cert.pem`과 `key.pem`을 생성합니다. 서버가 준비되면 다른 터미널에서 요청을 보냅니다.

```bash
# L4: 경로와 관계없이 TLS_BACKEND
curl -k https://localhost:8443/users
curl -k https://localhost:8443/orders

# L7: HTTP 경로에 따라 서로 다른 백엔드
curl -k https://localhost:9443/users
curl -k https://localhost:9443/orders
```

`-k`는 테스트용 자체 서명 인증서의 신뢰 검증을 생략합니다. 클라이언트와 TLS 종료 지점 사이의 HTTPS 암호화는 유지됩니다. 종료하려면 서버 터미널에서 `Ctrl+C`를 누릅니다.

## 검증 결과

| 요청 | 응답한 백엔드 |
| --- | --- |
| L4 `/users` | `TLS_BACKEND` |
| L4 `/orders` | `TLS_BACKEND` |
| L7 `/users` | `USERS` |
| L7 `/orders` | `ORDERS` |

각 응답은 백엔드 이름과 요청 경로를 출력합니다. 로그에서도 L4에는 TCP 연결 중계가, L7에는 복호화된 경로와 선택된 백엔드가 표시됩니다.

## 핵심 코드

L4는 TCP 소켓에서 받은 바이트를 다른 소켓으로 그대로 전달합니다. 암호화된 TLS 레코드 안의 HTTP 요청을 해석하지 않습니다.

```python
data = source.recv(65536)
target.sendall(data)
```

L7은 TLS 종료 뒤 HTTP 경로를 읽고 백엔드 포트를 선택합니다.

```python
if self.path.startswith("/users"):
    port, name = 9002, "USERS"
elif self.path.startswith("/orders"):
    port, name = 9003, "ORDERS"
```

## HTTP/HTTPS와 L4/L7: 코드에서 보는 핵심 차이

두 비교는 서로 다른 질문에 답합니다.

- **HTTP vs HTTPS**: 연결의 데이터가 TLS로 보호되는가? HTTPS는 HTTP를 TLS 연결 안에서 주고받으며, Python 서버에서는 소켓을 `SSLContext`로 감싸 TLS를 설정합니다.
- **L4 vs L7**: 중간 프록시가 어느 계층의 데이터를 이해하고 처리하는가? L4는 TCP 바이트를 전달하고, L7은 TLS를 종료한 뒤 HTTP 요청을 읽어 경로에 따라 라우팅합니다.

| 구분 | 코드에서 보는 차이 | 이 실습의 구현 |
| --- | --- | --- |
| HTTP / HTTPS | 서버 소켓에 TLS를 적용하는가 | 평문 서버는 `ThreadingHTTPServer`; HTTPS는 `tls_context().wrap_socket(..., server_side=True)` |
| L4 / L7 | 프록시가 HTTP 요청을 파싱하는가 | L4는 `recv()`/`sendall()`로 TCP 바이트 전달; L7은 `self.path`를 읽어 백엔드 선택 |
| TLS 종료 | TLS 복호화가 일어나는 지점 | L4는 백엔드 `:9001`; L7은 프록시 `:9443` |
| 백엔드 구간 | 프록시 이후의 전송 방식 | L4는 TLS 연결을 백엔드까지 전달; L7은 이 데모에서 평문 HTTP로 백엔드 호출 |

HTTPS 활성화의 핵심은 서버 소켓을 TLS로 감싸는 코드입니다.

```python
server = ThreadingHTTPServer(("127.0.0.1", port), handler)
server.socket = tls_context().wrap_socket(server.socket, server_side=True)
```

반면 L4와 L7의 핵심 차이는 프록시의 처리 코드입니다. 아래 첫 코드는 암호화된 TLS 바이트를 해석하지 않고 전달합니다. 둘째 코드는 TLS 종료 이후 얻은 HTTP 경로를 사용해 목적지를 선택합니다.

```python
# L4: TCP 바이트 그대로 전달
data = source.recv(65536)
target.sendall(data)

# L7: HTTP 경로를 기준으로 라우팅
if self.path.startswith("/users"):
    port, name = 9002, "USERS"
elif self.path.startswith("/orders"):
    port, name = 9003, "ORDERS"
```

따라서 **HTTPS라고 해서 L7인 것은 아닙니다**. HTTPS 연결도 L4에서 그대로 전달할 수 있고, L7 프록시는 TLS를 종료해 HTTP 내용을 확인할 수 있습니다. 이 실습에서는 L4/L7 예제가 모두 클라이언트와 첫 TLS 종료 지점 사이에 HTTPS를 사용하지만, 라우팅 능력 차이는 HTTP를 해석하는 코드가 프록시에 있는지로 결정됩니다.

## TLS 인증서 위치

| 항목 | L4 실습 | L7 실습 |
| --- | --- | --- |
| TLS 종료 위치 | Backend `:9001` | Proxy `:9443` |
| Proxy의 HTTP 경로 확인 | 아니요 | 예 |
| 프록시에서 백엔드까지 | HTTPS (암호화된 TCP 전달) | HTTP |
| 인증서 사용 위치 | TLS Backend | L7 Proxy |

## Python HTTP와 HTTPS 비교 실험

`http_https_benchmark.py`는 동일한 Python HTTP 핸들러와 응답 본문을 각각 HTTP와 HTTPS 로컬 서버에서 제공하고, 두 프로토콜의 요청 지연시간을 비교합니다. 두 서버 모두 매 요청 새 TCP 연결을 사용하고, HTTPS는 자체 서명 인증서를 로컬 클라이언트에 명시적으로 신뢰시킵니다. 인증서 검증을 끄지 않습니다.

```bash
python3 http_https_benchmark.py --requests 100 --warmup 10
```

프로그램은 JSON으로 요청 수, HTTP 상태코드, 응답 크기, 평균·중앙값·p95 지연시간 및 HTTPS 평균 지연시간의 HTTP 대비 증가율을 출력합니다. 측정은 순차 실행이며 워밍업 요청은 통계에서 제외됩니다. 필요하면 `--requests`와 `--warmup`으로 횟수를 조절할 수 있습니다.

### 결과 해석

두 프로토콜 모두 같은 상태코드와 응답 크기를 반환하는지 먼저 확인합니다. HTTPS는 TLS 핸드셰이크와 암복호화 비용이 더해지지만, 실측 차이는 기기 성능, TLS 구현, 연결 재사용 여부와 실행 환경에 따라 달라집니다. 따라서 한 번의 로컬 실행에서 나온 절대 지연시간이나 증가율을 일반적인 성능 수치로 해석하지 않습니다. 이 스크립트는 요청마다 새 연결을 만들어 핸드셰이크 비용이 포함되는 조건을 비교합니다.

이 실험은 암호화 방식의 차이를 관찰하는 것입니다. 앞의 L4/L7 실습은 TLS 종료 위치와 HTTP 경로 라우팅 능력의 차이를 보여주며, 둘은 서로 다른 비교 축입니다.

## 클라우드/쿠버네티스 개념과 연결

이 실험의 L4 TCP 전달 방식은 AWS NLB의 TCP 리스너와, L7 HTTP 경로 라우팅은 ALB의 HTTP/HTTPS 리스너 규칙과 개념적으로 비교할 수 있습니다. Kubernetes에서도 Service는 주로 네트워크 연결을 노출하고, Ingress는 HTTP(S) 라우팅 규칙을 표현합니다. 실제 제품의 기능과 동작은 이 간단한 로컬 실습과 완전히 같지는 않지만, 계층별 차이를 이해하는 출발점으로 활용할 수 있습니다.

## 요구사항

- Python 3.8 이상
- OpenSSL (`openssl` 명령이 PATH에 있어야 함)
- 포트 `8443`, `9443`, `9001`, `9002`, `9003` 사용 가능

Python 외부 패키지는 필요하지 않습니다. 생성되는 로컬 인증서와 개인키는 `.gitignore` 대상입니다.
