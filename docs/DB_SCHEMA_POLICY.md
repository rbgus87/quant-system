# DB 스키마 변경 정책

## 원칙

dev/prod(exe) 분리 운용 환경에서 DB 호환성 유지를 위해 다음 룰을 따른다.

운용 환경:
- **exe (prod)**: 빌드 시점에 코드 freeze. 분기 단위로만 재빌드.
- **dev**: 코드 지속 변경. 같은 `data/quant.db` 공유.
- 따라서 dev에서의 DB 변경이 exe 실행에 영향을 줄 수 있음.

## 허용 (✅ 자유)

- 신규 테이블 추가 (`CREATE TABLE IF NOT EXISTS`)
- 기존 테이블에 **nullable** 컬럼 추가 (`ALTER TABLE ... ADD COLUMN`)
- 인덱스 추가 (`CREATE INDEX IF NOT EXISTS`)
- 데이터 행 추가/수정 (백필, 임포트 등)

## 금지 (❌ 절대)

- 기존 컬럼 삭제
- 컬럼 타입 변경 (단, 호환 가능한 확장은 사전 검토)
- 컬럼명 변경
- 기존 `UNIQUE` 제약 변경
- 테이블 삭제

## 절차

신규 컬럼/테이블 추가 시:
1. `data/storage.py` 의 SQLAlchemy 모델 수정
2. `_migrate_*` 메서드에 `ALTER TABLE` 또는 `CREATE TABLE IF NOT EXISTS` 추가
3. `nullable=True` 또는 `default` 값 명시 (기존 행 호환)
4. 마이그레이션 idempotent 확인 (재실행 안전)

## 파괴적 변경이 정말 필요한 경우

1. 새 컬럼/테이블로 우회 (예: `old_xxx` → `new_xxx` 병행 유지)
2. 분기 리밸런싱 종료 후 exe 재빌드 시점에 cleanup
3. **절대 dev에서 직접 `ALTER DROP` 금지**

## 변경 이력

| 날짜 | 변경 | 비고 |
|------|------|------|
| 2026-05-12 | 정책 수립 | dev/prod 분리 시작 |
