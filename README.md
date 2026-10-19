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

## TLS 인증서 위치

| 항목 | L4 실습 | L7 실습 |
| --- | --- | --- |
| TLS 종료 위치 | Backend `:9001` | Proxy `:9443` |
| Proxy의 HTTP 경로 확인 | 아니요 | 예 |
| 프록시에서 백엔드까지 | HTTPS (암호화된 TCP 전달) | HTTP |
| 인증서 사용 위치 | TLS Backend | L7 Proxy |

## 클라우드/쿠버네티스 개념과 연결

이 실험의 L4 TCP 전달 방식은 AWS NLB의 TCP 리스너와, L7 HTTP 경로 라우팅은 ALB의 HTTP/HTTPS 리스너 규칙과 개념적으로 비교할 수 있습니다. Kubernetes에서도 Service는 주로 네트워크 연결을 노출하고, Ingress는 HTTP(S) 라우팅 규칙을 표현합니다. 실제 제품의 기능과 동작은 이 간단한 로컬 실습과 완전히 같지는 않지만, 계층별 차이를 이해하는 출발점으로 활용할 수 있습니다.

## 요구사항

- Python 3.8 이상
- OpenSSL (`openssl` 명령이 PATH에 있어야 함)
- 포트 `8443`, `9443`, `9001`, `9002`, `9003` 사용 가능

Python 외부 패키지는 필요하지 않습니다. 생성되는 로컬 인증서와 개인키는 `.gitignore` 대상입니다.
