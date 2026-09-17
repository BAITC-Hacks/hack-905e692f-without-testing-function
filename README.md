# MedFlow AI — GovTech Camp, Case 1

Панель операционной аналитики стационаров: снимок очереди, дневные регистрации, статистические сигналы и краткосрочный прогноз. Решения принимает специалист после проверки первичных данных.

## Запуск

Требуются Python 3.12+, Node.js 18+ и npm.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --app-dir apps/api --reload --port 8000
```

Во втором терминале:

```bash
cd frontend
npm install
npm run dev
```

Панель: http://localhost:3000. API: http://localhost:8000/docs. `NEXT_PUBLIC_API_URL` задаёт адрес API, по умолчанию `http://127.0.0.1:8000/api/v1`.

## Данные и ML

Разрешённые исходные выгрузки размещаются в `data/raw/`; медицинские данные, БД и артефакты исключены из Git. `python scripts/profile_data.py` выполняет потоковое профилирование. Синтетический файл не используется в API или обучении.

CSV очереди читается потоково один раз для построения агрегатов. Кеш `ml/artifacts/queue_arrivals_aggregates.joblib` проверяется по имени, размеру, времени изменения источника и `MEDFLOW_MAX_RAW_ROWS`. Модель Ridge и метаданные сохраняются отдельно. HTTP-запросы используют агрегаты в памяти. После замены raw-файлов перезапустите API; нельзя изменять источник во время обучения. Артефакты joblib должны быть только доверенными локальными файлами.

Цель `daily_waiting_registrations` — число регистраций за день среди записей в предоставленном снимке. Это **не прогноз будущего размера очереди** и не полный исторический поток поступлений: уже выбывшие записи отсутствуют. Текущая очередь показывается отдельно. Изменение прогноза сравнивается только с последним дневным значением; риск сравнивает дневное значение с предыдущими днями.

Признаки: организация назначения, день недели, lag-1, lag-7, среднее за предыдущие 7 дней. Финальные 20% дат — хронологическая validation. Основные метрики MAE/RMSE; baseline — последнее дневное значение. Вторичный MAPE использует max(target,1) в знаменателе и нестабилен около нуля. Интервалы основаны на остатках validation и приближённые. Вклады и модули коэффициентов не доказывают причинность и зависят от масштаба признаков.

В raw обнаружены коды `10,11,15,19,23,27,31,33,35,39,43,47,55,59,61,62,63,71,75,79`. Отдельного справочника там нет. Названия сверены с [КАТО НК РК 11-2025, БНС](https://stat.gov.kz/ru/classifiers/statistical/21/); нормализация находится в `apps/api/app/core/regions.py`. Неизвестные коды показываются явно, исходный код сохраняется. Регион — происхождение пациентов. Регион организации в таблице — наиболее частый регион происхождения её пациентов, а не адрес МО. Региональные суммы используют фактический регион каждой записи.

## Вход

`/register` сохраняет id, уникальный нормализованный email, scrypt password_hash, имя и created_at в SQLite `data/processed/auth.sqlite3`; путь можно изменить через `MEDFLOW_DB`. Пароли 8–128 символов, случайная соль на пользователя. `/login` проверяет хеш и создаёт случайный токен сессии на 8 часов. В БД хранится только SHA-256 токена. `/auth/logout` отзывает сессию. Аналитические API требуют `Authorization: Bearer <token>`. Регистрация возвращает 400 при неверных данных, 409 при повторном email; вход — 401 при неверных учётных данных.

ЭЦП DEMO — отдельная явно демонстрационная сессия. Сертификаты, подпись и интеграция НУЦ РК отсутствуют. При внешнем размещении требуется HTTPS и ограничение доступа к исходникам и БД.

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

Use Python 3.12+, Node.js 18+ and npm. Create a virtual environment, install with `pip install -e .`, then run `uvicorn app.main:app --app-dir apps/api --reload --port 8000`. In `frontend/`, run `npm install` and `npm run dev`. Open http://localhost:3000; API docs are at http://localhost:8000/docs. `NEXT_PUBLIC_API_URL` overrides the API base URL.

Place authorized extracts in `data/raw/`. Raw healthcare data, SQLite files and model artifacts are ignored by Git. Profiling and CSV aggregation stream records. Persisted aggregates are invalidated by source file metadata and row limit; request handlers reuse in-memory aggregates. Restart the API after replacing source files. Only trusted locally generated joblib artifacts may be loaded.

## Authentication

Registration persists users in SQLite with unique normalized email, salted scrypt hash, name and creation timestamp. Passwords require 8–128 characters. Login creates a revocable opaque session valid for 8 hours; only token hashes are stored. Analytics endpoints require Bearer authorization. Logout revokes the session. `MEDFLOW_DB` overrides `data/processed/auth.sqlite3`. Validation returns 400, duplicate email 409, invalid credentials 401. ECP DEMO is a separate presentation login without certificates, signatures or NCA RK integration. External deployments require HTTPS and restricted source/database access.

## Analytics and limitations

The target `daily_waiting_registrations` counts dated registrations remaining in one waiting-list snapshot. It does not forecast total queue size or recover the full historical arrival stream. Queue totals and daily forecasts are separate; forecast changes and risk use comparable daily values only.

Ridge uses destination code, weekday, lag-1, lag-7 and trailing seven-day mean. The final 20% of dates form chronological validation. MAE/RMSE are primary metrics; lag-1 persistence is the baseline. Secondary MAPE uses max(target,1), and is unstable near zero. Residual intervals are approximate; linear contributions are associations, not causes.

Observed region prefixes were checked against official KATO; unknown codes retain an explicit unknown label and their source code. Regions describe patient origin, not destination location. Organization grouping uses the dominant patient-origin region; regional totals count each record's actual origin.

Organization and anomaly endpoints paginate server-side with `page`, `page_size`, `total`, `total_pages`, `items`; the default is 25 and maximum 100. Search, filters and sorting run before pagination. The browser receives bounded pages and compact aggregates, never raw extracts.

Validate with `.venv/bin/pytest`, `.venv/bin/ruff check .`, and `npm run typecheck` / `npm run build` inside `frontend/`.
