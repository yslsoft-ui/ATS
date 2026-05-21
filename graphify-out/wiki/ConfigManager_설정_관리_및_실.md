# ConfigManager 설정 관리 및 실

> 32 nodes · cohesion 0.50

## Key Concepts

- **ConfigManager** (17 connections) — `src/config/manager.py`
- **.reload()** (7 connections) — `src/config/manager.py`
- **._merge_env_vars()** (6 connections) — `src/config/manager.py`
- **.get()** (4 connections) — `src/config/manager.py`
- **.start_watching()** (4 connections) — `src/config/manager.py`
- **.update()** (4 connections) — `src/config/manager.py`
- **.save()** (4 connections) — `src/config/manager.py`
- **._substitute_env_vars()** (3 connections) — `src/config/manager.py`
- **.subscribe()** (3 connections) — `src/config/manager.py`
- **._watch_loop()** (3 connections) — `src/config/manager.py`
- **test_kis_token()** (2 connections) — `scratch/test_kis_auth.py`
- **.__init__()** (2 connections) — `src/config/manager.py`
- **특정 설정을 업데이트하고 파일로 즉시 저장합니다.** (2 connections) — `src/config/manager.py`
- **test_kis_auth.py** (1 connections) — `scratch/test_kis_auth.py`
- **manager.py** (1 connections) — `src/config/manager.py`
- **.stop_watching()** (1 connections) — `src/config/manager.py`
- **YAML 설정을 관리하고, 실시간 변경 감지 및 환경 변수 치환을 수행합니다.** (1 connections) — `src/config/manager.py`
- **설정 파일을 다시 읽고 환경 변수 치환 및 병합을 수행합니다.** (1 connections) — `src/config/manager.py`
- **설정 내의 ${VAR_NAME} 형식을 실제 환경 변수 값으로 치환합니다.** (1 connections) — `src/config/manager.py`
- **환경 변수를 설정에 병합합니다 (형식: SECTION__KEY).** (1 connections) — `src/config/manager.py`
- **점(.)으로 구분된 키를 사용하여 설정값을 가져옵니다 (예: 'system.db_path').** (1 connections) — `src/config/manager.py`
- **설정 변경 시 호출될 콜백을 등록합니다.** (1 connections) — `src/config/manager.py`
- **백그라운드에서 파일 변경을 감시합니다.** (1 connections) — `src/config/manager.py`
- **특정 설정을 업데이트하고 파일로 즉시 저장합니다.** (1 connections) — `src/config/manager.py`
- **현재 메모리의 설정을 파일로 저장합니다.** (1 connections) — `src/config/manager.py`
- *... and 7 more nodes in this community*

## Relationships

- [[Community 60]] (1 shared connections)
- [[매매 전략 알고리즘]] (1 shared connections)

## Source Files

- `scratch/test_kis_auth.py`
- `src/config/manager.py`

## Audit Trail

- EXTRACTED: 76 (95%)
- INFERRED: 4 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*