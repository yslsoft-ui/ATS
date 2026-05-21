# Community 55

> 10 nodes · cohesion 0.50

## Key Concepts

- **DBWriter** (5 connections) — `src/database/db_writer.py`
- **UpbitCollector** (5 connections) — `src/collector/upbit_ws.py`
- **.run()** (3 connections) — `src/database/db_writer.py`
- **.flush()** (2 connections) — `src/database/db_writer.py`
- **upbit_ws.py** (2 connections) — `src/collector/upbit_ws.py`
- **.connect_and_listen()** (2 connections) — `src/collector/upbit_ws.py`
- **main_run()** (2 connections) — `src/collector/upbit_ws.py`
- **db_writer.py** (1 connections) — `src/database/db_writer.py`
- **.__init__()** (1 connections) — `src/database/db_writer.py`
- **.__init__()** (1 connections) — `src/collector/upbit_ws.py`

## Relationships

- [[ATS 텔레]] (1 shared connections)
- [[Community 43]] (1 shared connections)

## Source Files

- `src/collector/upbit_ws.py`
- `src/database/db_writer.py`

## Audit Trail

- EXTRACTED: 20 (83%)
- INFERRED: 4 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*