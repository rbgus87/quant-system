# Oracle Cloud Always Free 배포 가이드

퀀트 자동매매 스케줄러를 Oracle Cloud Always Free 티어에 배포하는 방법을 설명합니다.

## 목차

1. [왜 Oracle Cloud인가](#1-왜-oracle-cloud인가)
2. [도입 계획](#2-도입-계획)
3. [Oracle Cloud 가입](#3-oracle-cloud-가입)
4. [VM 인스턴스 생성](#4-vm-인스턴스-생성)
5. [서버 초기 설정](#5-서버-초기-설정)
6. [프로젝트 배포](#6-프로젝트-배포)
7. [systemd 서비스 등록](#7-systemd-서비스-등록)
8. [모니터링 및 유지보수](#8-모니터링-및-유지보수)
9. [보안 설정](#9-보안-설정)
10. [트러블슈팅](#10-트러블슈팅)

---

## 1. 왜 Oracle Cloud인가

### 무료 티어 비교

| 서비스 | 스펙 | 기간 | 한국 리전 | 24/7 가능 |
|--------|------|------|:---------:|:---------:|
| **Oracle Cloud** | **ARM 4 OCPU / 24GB RAM** | **영구** | **서울/춘천** | **O** |
| Google Cloud | 0.25 vCPU / 1GB RAM | 영구 | X (미국만) | O |
| AWS | t2.micro 1 vCPU / 1GB | 12개월 | O | O |
| Render | 0.1 CPU / 512MB | 영구 | X | X (sleep) |

### Oracle Cloud Always Free 선택 이유

1. **영구 무료**: 12개월 한정이 아닌 평생 무료
2. **서울/춘천 리전**: KRX API, 키움 API 접근 지연 최소 (~5ms)
3. **충분한 스펙**: ARM 24GB RAM을 최대 4개 VM으로 분할 가능
4. **코드 변경 불필요**: 현재 코드 그대로 배포 가능

### 권장 VM 구성

```
VM 1: 퀀트 스케줄러 (메인)
  - 1 OCPU / 6GB RAM / 47GB 디스크
  - Ubuntu 22.04 (aarch64)
  - scheduler/main.py + SQLite

VM 2: Streamlit 대시보드 (선택)
  - 1 OCPU / 6GB RAM / 47GB 디스크
  - 외부 접근용 (포트 8501)
```

---

## 2. 도입 계획

### 단계적 전환

```
Phase 1: 로컬 PC (현재 ~ 1-2개월)
  ├── 모의투자 실행
  ├── 버그 수정 / 안정화
  └── 매일 결과 직접 확인

Phase 2: Oracle Cloud 이전 (안정화 후)
  ├── VM 생성 및 환경 설정
  ├── 코드 배포 + systemd 등록
  └── 모의투자 병행 운영 (로컬 + 클라우드)

Phase 3: 클라우드 단독 운영 (실전 투자)
  ├── Oracle Cloud: 실전 매매 (메인)
  ├── 로컬 PC: 개발 + 백테스트 + 모니터링
  └── 텔레그램 알림으로 원격 확인
```

### 이전 시점 기준

- [ ] 1주일 이상 에러 없이 정상 동작
- [ ] 코드 수정 빈도가 주 1회 이하로 감소
- [ ] 모의투자 결과에 확신이 생김
- [ ] 실전 투자 전환 직전

---

## 3. Oracle Cloud 가입

### 3.1 사전 준비

- 이메일 주소
- 신용카드 (검증용, 과금 안 됨)
- 휴대폰 번호

### 3.2 가입 절차

1. https://cloud.oracle.com 접속
2. "Start for Free" 클릭
3. 홈 리전 선택: **South Korea Central (Seoul)** 또는 **South Korea North (Chuncheon)**
   - 가입 후 홈 리전 변경 불가 → 서울 우선 선택
4. 계정 정보 입력 + 신용카드 등록
5. 이메일 인증 완료

> **주의**: 홈 리전은 변경 불가합니다. 반드시 서울(ap-seoul-1) 또는 춘천(ap-chuncheon-1)을 선택하세요.

### 3.3 Always Free 리소스 확인

가입 후 콘솔에서 확인:
- Compute VM: ARM (Ampere A1) 최대 4 OCPU / 24GB RAM
- Boot Volume: 200GB (합계)
- Networking: 10TB/월 아웃바운드

---

## 4. VM 인스턴스 생성

### 4.1 콘솔에서 생성

1. Oracle Cloud Console → Compute → Instances → Create Instance

2. 설정값:

| 항목 | 값 |
|------|-----|
| Name | `quant-scheduler` |
| Image | Ubuntu 22.04 (aarch64) |
| Shape | VM.Standard.A1.Flex |
| OCPU | 1 |
| Memory | 6 GB |
| Boot Volume | 47 GB |
| Networking | Public subnet + Public IP |

3. SSH 키 생성 또는 업로드
   - "Generate a key pair" 선택 → 프라이빗 키 다운로드
   - 또는 기존 SSH 공개키 업로드

4. "Create" 클릭

> **팁**: 서울 리전에서 ARM VM 생성이 실패하면 (용량 부족), 10~30분 후 재시도하거나 춘천 리전을 사용하세요.

### 4.2 보안 목록 (방화벽) 설정

콘솔 → Networking → Virtual Cloud Networks → Security Lists

| 규칙 | 프로토콜 | 포트 | 소스 | 용도 |
|------|---------|------|------|------|
| SSH | TCP | 22 | 내 IP만 | SSH 접속 |
| Streamlit | TCP | 8501 | 0.0.0.0/0 (또는 내 IP만) | 대시보드 (선택) |

> 키움 API, KRX API, Telegram API는 **아웃바운드**이므로 별도 규칙 불필요.

---

## 5. 서버 초기 설정

### 5.1 SSH 접속

```bash
# 다운로드한 프라이빗 키 권한 설정
chmod 400 ~/ssh-key-quant.key

# 접속 (공인 IP는 콘솔에서 확인)
ssh -i ~/ssh-key-quant.key ubuntu@<PUBLIC_IP>
```

### 5.2 시스템 업데이트 + 기본 패키지

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git sqlite3 htop
```

### 5.3 Python 버전 확인

```bash
python3 --version
# Ubuntu 22.04: Python 3.10.x (충분)
```

> Python 3.14를 사용하고 싶다면 pyenv 또는 deadsnakes PPA를 설치할 수 있으나, 3.10~3.12로도 코드가 정상 동작합니다.

### 5.4 타임존 설정

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl
# Time zone: Asia/Seoul (KST, +0900)
```

### 5.5 스왑 설정 (안전장치)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 6. 프로젝트 배포

### 6.1 코드 가져오기

```bash
# 방법 A: Git (권장)
cd /opt
sudo mkdir quant-system && sudo chown ubuntu:ubuntu quant-system
git clone <REPO_URL> /opt/quant-system

# 방법 B: SCP (Git 미사용 시)
# 로컬에서:
scp -i ~/ssh-key-quant.key -r D:/project/quant-system/ ubuntu@<PUBLIC_IP>:/opt/quant-system/
```

### 6.2 가상환경 + 의존성

```bash
cd /opt/quant-system
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> ARM(aarch64)에서 numpy, pandas 설치 시 빌드가 필요할 수 있습니다.
> 실패 시: `sudo apt install -y python3-dev build-essential` 후 재시도

### 6.3 환경변수 설정

```bash
cp .env.example .env
nano .env
```

```env
# 필수
KIWOOM_APP_KEY=your_app_key
KIWOOM_APP_SECRET=your_app_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 선택
IS_PAPER_TRADING=true
LOG_LEVEL=INFO
DB_PATH=/opt/quant-system/data/quant.db
CONFIG_PATH=/opt/quant-system/config/config.yaml

# KRX Open API
KRX_OPENAPI_KEY=your_krx_key
```

### 6.4 동작 확인

```bash
source venv/bin/activate

# 설정 확인
python scheduler/main.py --dry-run

# 스크리닝 테스트 (매매 없음)
python scheduler/main.py --screen-only

# 테스트 실행
python -m pytest tests/ -v
```

---

## 7. systemd 서비스 등록

### 7.1 서비스 파일 생성

```bash
sudo tee /etc/systemd/system/quant-scheduler.service << 'EOF'
[Unit]
Description=Quant Trading Scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/quant-system
EnvironmentFile=/opt/quant-system/.env
ExecStart=/opt/quant-system/venv/bin/python scheduler/main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

# 리소스 제한 (안전장치)
MemoryMax=2G
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF
```

### 7.2 서비스 등록 및 시작

```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-scheduler
sudo systemctl start quant-scheduler
```

### 7.3 상태 확인

```bash
# 서비스 상태
sudo systemctl status quant-scheduler

# 실시간 로그
sudo journalctl -u quant-scheduler -f

# 최근 100줄 로그
sudo journalctl -u quant-scheduler -n 100 --no-pager
```

### 7.4 서비스 관리 명령어

```bash
# 재시작 (코드 업데이트 후)
sudo systemctl restart quant-scheduler

# 중지
sudo systemctl stop quant-scheduler

# 로그 확인 (오늘)
sudo journalctl -u quant-scheduler --since today
```

---

## 8. 모니터링 및 유지보수

### 8.1 코드 업데이트 절차

```bash
cd /opt/quant-system

# 1. 서비스 중지 (장 마감 후 권장)
sudo systemctl stop quant-scheduler

# 2. 코드 업데이트
git pull origin main

# 3. 의존성 업데이트 (필요시)
source venv/bin/activate
pip install -r requirements.txt

# 4. 서비스 재시작
sudo systemctl start quant-scheduler

# 5. 로그 확인
sudo journalctl -u quant-scheduler -f
```

### 8.2 DB 백업 (cron 자동화)

```bash
# 매일 새벽 3시 DB 백업
crontab -e
```

```cron
0 3 * * * cp /opt/quant-system/data/quant.db /opt/quant-system/data/backup/quant_$(date +\%Y\%m\%d).db
0 4 * * 0 find /opt/quant-system/data/backup/ -name "*.db" -mtime +30 -delete
```

```bash
# 백업 폴더 생성
mkdir -p /opt/quant-system/data/backup
```

### 8.3 디스크 사용량 확인

```bash
# 전체 디스크
df -h

# 프로젝트 폴더
du -sh /opt/quant-system/
du -sh /opt/quant-system/data/quant.db
```

### 8.4 헬스체크 스크립트 (선택)

```bash
# /opt/quant-system/scripts/healthcheck.sh
#!/bin/bash

if ! systemctl is-active --quiet quant-scheduler; then
    echo "$(date): 스케줄러 중지 감지, 재시작" >> /var/log/quant-health.log
    systemctl restart quant-scheduler

    # 텔레그램 알림
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="[경고] 퀀트 스케줄러가 중지되어 자동 재시작했습니다."
fi
```

```bash
chmod +x /opt/quant-system/scripts/healthcheck.sh

# 5분마다 실행
crontab -e
# 추가:
*/5 * * * * /opt/quant-system/scripts/healthcheck.sh
```

### 8.5 로그 로테이션

systemd journal은 자동 관리되지만, 디스크 절약을 위해:

```bash
# journal 크기 제한 (500MB)
sudo journalctl --vacuum-size=500M
```

---

## 9. 보안 설정

### 9.1 SSH 보안 강화

```bash
sudo nano /etc/ssh/sshd_config
```

```
# 비밀번호 로그인 비활성화 (키 인증만)
PasswordAuthentication no

# 루트 로그인 비활성화
PermitRootLogin no

# SSH 포트 변경 (선택)
# Port 2222
```

```bash
sudo systemctl restart sshd
```

### 9.2 방화벽 (UFW)

```bash
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 8501/tcp  # Streamlit (필요시)
sudo ufw enable
sudo ufw status
```

### 9.3 .env 파일 보호

```bash
chmod 600 /opt/quant-system/.env
```

### 9.4 자동 보안 업데이트

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 10. 트러블슈팅

### VM 생성 실패 ("Out of capacity")

서울 리전의 ARM VM 용량이 부족한 경우:
- 10~30분 간격으로 재시도
- 춘천 리전(ap-chuncheon-1)으로 변경
- OCPU/RAM을 줄여서 시도 (최소 1 OCPU / 1GB)

### ARM 호환성 문제

일부 Python 패키지가 aarch64 빌드를 제공하지 않을 수 있음:

```bash
# 빌드 도구 설치
sudo apt install -y python3-dev build-essential libffi-dev

# 특정 패키지 재설치
pip install --no-binary :all: <package_name>
```

### 메모리 부족 (OOM Kill)

```bash
# OOM 로그 확인
sudo dmesg | grep -i "out of memory"

# 해결: 스왑 추가 (이미 설정했다면 크기 증가)
sudo swapoff /swapfile
sudo fallocate -l 4G /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### 서비스가 반복 재시작

```bash
# 상세 로그 확인
sudo journalctl -u quant-scheduler -n 200 --no-pager

# 수동 실행으로 에러 확인
sudo systemctl stop quant-scheduler
cd /opt/quant-system
source venv/bin/activate
python scheduler/main.py
```

### 키움 API 연결 실패

- mockapi.kiwoom.com (모의) / api.kiwoom.com (실전) 접근 확인:
  ```bash
  curl -v https://mockapi.kiwoom.com
  ```
- .env의 KIWOOM_APP_KEY, KIWOOM_APP_SECRET 확인
- 키움 API 토큰 만료 시 자동 갱신 로직 확인

### Oracle Cloud VM idle 정지 정책

Oracle은 Always Free VM이 7일 이상 CPU 사용률 15% 미만이면 정지(stop) 경고를 보냅니다.

APScheduler가 상주하고 있으면 보통 문제없지만, 안전을 위해:

```bash
# 최소 CPU 활동 보장 (선택)
crontab -e
# 매 시간 경량 작업 실행
0 * * * * /opt/quant-system/venv/bin/python -c "import time; time.sleep(1)"
```

> 실제로 스케줄러가 돌아가고 있으면 이 문제는 거의 발생하지 않습니다.

---

## 부록: 빠른 참조 명령어

```bash
# === 서비스 관리 ===
sudo systemctl status quant-scheduler    # 상태 확인
sudo systemctl restart quant-scheduler   # 재시작
sudo systemctl stop quant-scheduler      # 중지
sudo journalctl -u quant-scheduler -f    # 실시간 로그

# === 코드 업데이트 ===
cd /opt/quant-system
sudo systemctl stop quant-scheduler
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl start quant-scheduler

# === 수동 실행 ===
cd /opt/quant-system && source venv/bin/activate
python scheduler/main.py --dry-run       # 설정 확인
python scheduler/main.py --screen-only   # 스크리닝만
python scheduler/main.py --now           # 즉시 리밸런싱

# === 모니터링 ===
htop                                     # 시스템 리소스
df -h                                    # 디스크 사용량
du -sh data/quant.db                     # DB 크기
sudo journalctl -u quant-scheduler --since today  # 오늘 로그
```
