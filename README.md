# MedFlow AI — GovTech Camp, Case 1

Панель операционной аналитики стационаров: снимок очереди, дневные регистрации, статистические сигналы и краткосрочный прогноз. Решения принимает специалист после проверки первичных данных.

## Запуск

Требуются Python 3.12+, Node.js 18+ и npm.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Set ADMIN_EMAIL and ADMIN_PASSWORD_HASH in .env (see command below).
set -a; source .env; set +a
python -m uvicorn --app-dir apps/api app.main:app --reload --port 8000
```

Во втором терминале:

```bash
cd frontend
npm install
npm run dev
```

Панель: http://localhost:3000. API: http://localhost:8000/docs. По умолчанию Next.js проксирует `/api/v1` на `API_INTERNAL_URL` (`http://127.0.0.1:8000`); `NEXT_PUBLIC_API_URL` может задать публичный адрес API.

## Данные и ML

Разрешённые исходные выгрузки размещаются в `data/raw/`; медицинские данные, БД и артефакты исключены из Git. `python scripts/profile_data.py` выполняет потоковое профилирование. Синтетический файл не используется в API или обучении.

CSV очереди читается потоково один раз для построения агрегатов. Кеш `ml/artifacts/queue_arrivals_aggregates.joblib` проверяется по fingerprint и `MEDFLOW_MAX_RAW_ROWS`. HTTP-запросы никогда не обучают модель: backend только загружает локальный совместимый artifact. Отсутствующий artifact возвращает `MODEL_NOT_READY`, источник — `DATA_UNAVAILABLE`. Обучение запускается явно командой из [ML methodology](docs/ML_METHODOLOGY.md).

Цель `daily_waiting_registrations` — число регистраций за день среди записей в предоставленном снимке. Это **не прогноз будущего размера очереди** и не полный исторический поток поступлений: уже выбывшие записи отсутствуют. Текущая очередь показывается отдельно. Изменение прогноза сравнивается только с последним дневным значением; риск сравнивает дневное значение с предыдущими днями.

Признаки: организация назначения, день недели, lag-1, lag-7, среднее за предыдущие 7 дней. Финальные 20% дат — хронологическая validation. Метрики: MAE, RMSE и WAPE; baseline — последнее дневное значение. Интервалы основаны на остатках validation и приближённые. Local contribution Ridge вычисляется как transformed value × coefficient. Вклады и модули коэффициентов не доказывают причинность.

В raw обнаружены коды `10,11,15,19,23,27,31,33,35,39,43,47,55,59,61,62,63,71,75,79`. Отдельного справочника там нет. Названия сверены с [КАТО НК РК 11-2025, БНС](https://stat.gov.kz/ru/classifiers/statistical/21/); нормализация находится в `apps/api/app/core/regions.py`. Неизвестные коды показываются явно, исходный код сохраняется. Регион — происхождение пациентов. Регион организации в таблице — наиболее частый регион происхождения её пациентов, а не адрес МО. Региональные суммы используют фактический регион каждой записи.

## Вход

Единственный администратор задаётся в `.env`: `ADMIN_EMAIL` и `ADMIN_PASSWORD_HASH`. Пароль в `.env` не хранится. Создайте хеш интерактивно: `python apps/api/scripts/generate_password_hash.py`, затем вставьте напечатанный `scrypt$...` в одинарных кавычках в `ADMIN_PASSWORD_HASH`. `/login` проверяет пароль через scrypt и создаёт случайный токен сессии на 8 часов. В SQLite `data/processed/auth.sqlite3` хранятся только SHA-256 хеши токенов сессии; путь можно изменить через `MEDFLOW_DB`. `/auth/logout` отзывает сессию. Аналитические API требуют `Authorization: Bearer <token>`. Некорректные учётные данные всегда возвращают 401 без уточнения причины.

ЭЦП DEMO — отдельная явно демонстрационная сессия. Сертификаты, подпись и интеграция НУЦ РК отсутствуют. При внешнем размещении требуется HTTPS и ограничение доступа к исходникам и БД.

## Решения, отчёты и API

Для high/critical риска интерфейс создаёт только черновик `DecisionAction`; реальное административное действие не выполняется. Статусы: `draft`, `approved`, `rejected`, `completed`. Создание и смена статуса, отчёты, экспорты и вход записываются в audit log без секретов.

Отчёт сохраняет неизменяемый снимок активных фильтров и экспортируется в CSV, XLSX и печатную HTML-версию для сохранения браузером в PDF. `report_id + SHA-256` используется только как **DEMO verification**, не как ЭЦП.

Карта работает локально; источник и лицензия описаны в [docs/GIS_SOURCE.md](docs/GIS_SOURCE.md). Data Sources перечисляет только реально найденные файлы и явно сообщает, что real-time integration не подключена.

## Пагинация и проверки

`GET /api/v1/organizations`: `page`, `page_size`, `region_id`, `risk`, `search`, `sort` (`risk`, `name`, `waiting`). Фильтрация и сортировка предшествуют пагинации. Ответ: `page`, `page_size`, `total`, `total_pages`, `items`. По умолчанию 25, максимум 100, UI предлагает 25/50/100. `/anomalies` имеет тот же формат страниц. `/summary` и `/regions` возвращают только компактные агрегаты; `/history` ограничен 84 днями. Raw не отправляется в браузер.

```bash
.venv/bin/pytest
.venv/bin/ruff check .
cd frontend
npm run typecheck
npm run build
```

Каталоги: `apps/api/` — FastAPI и auth; `frontend/` — Next.js; `ml/` — признаки и артефакты; `data/` — локальные источники; `docs/` — [архитектура](docs/ARCHITECTURE.md), [методология](docs/ML_METHODOLOGY.md), [конфиденциальность](docs/PRIVACY.md). `docker compose up --build` запускает API.

---

# English

MedFlow AI is a GovTech Camp Case 1 hospital operations dashboard. It shows the supplied queue snapshot, daily registrations, anomaly signals and short-term daily forecasts. Specialists must verify signals against source data.

## Run locally

Use Python 3.12+, Node.js 18+ and npm. From the repository root, create a virtual environment, install with `pip install -e .`, copy `.env.example` to `.env`, set `ADMIN_EMAIL` and a generated `ADMIN_PASSWORD_HASH`, load it with `set -a; source .env; set +a`, then run `python -m uvicorn --app-dir apps/api app.main:app --reload --port 8000`. In `frontend/`, run `npm install` and `npm run dev`. Open http://localhost:3000; API docs are at http://localhost:8000/docs. `NEXT_PUBLIC_API_URL` overrides the API base URL.

Place authorized extracts in `data/raw/`. Raw healthcare data, SQLite files and model artifacts are ignored by Git. Profiling and CSV aggregation stream records. Persisted aggregates are invalidated by source file metadata and row limit; request handlers reuse in-memory aggregates. Restart the API after replacing source files. Only trusted locally generated joblib artifacts may be loaded.

## Authentication

One administrator is configured through `ADMIN_EMAIL` and `ADMIN_PASSWORD_HASH` in `.env`; never put the plaintext password there. Run `python apps/api/scripts/generate_password_hash.py` and place its output in `ADMIN_PASSWORD_HASH`. Login verifies the salted scrypt hash and creates a revocable opaque session valid for 8 hours; only token hashes are stored. Analytics endpoints require Bearer authorization. Logout revokes the session. `MEDFLOW_DB` overrides `data/processed/auth.sqlite3`. Invalid credentials always return 401. ECP DEMO is a separate presentation login without certificates, signatures or NCA RK integration. External deployments require HTTPS and restricted source/database access.

## Analytics and limitations

The target `daily_waiting_registrations` counts dated registrations remaining in one waiting-list snapshot. It does not forecast total queue size or recover the full historical arrival stream. Queue totals and daily forecasts are separate; forecast changes and risk use comparable daily values only.

Ridge uses destination code, weekday, lag-1, lag-7 and trailing seven-day mean. The final 20% of dates form chronological validation. MAE/RMSE are primary metrics; lag-1 persistence is the baseline. Secondary MAPE uses max(target,1), and is unstable near zero. Residual intervals are approximate; linear contributions are associations, not causes.

Observed region prefixes were checked against official KATO; unknown codes retain an explicit unknown label and their source code. Regions describe patient origin, not destination location. Organization grouping uses the dominant patient-origin region; regional totals count each record's actual origin.

Organization and anomaly endpoints paginate server-side with `page`, `page_size`, `total`, `total_pages`, `items`; the default is 25 and maximum 100. Search, filters and sorting run before pagination. The browser receives bounded pages and compact aggregates, never raw extracts.

Validate with `.venv/bin/pytest`, `.venv/bin/ruff check .`, and `npm run typecheck` / `npm run build` inside `frontend/`.
