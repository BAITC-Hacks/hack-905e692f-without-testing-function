"use client";
import { useEffect, useState } from "react";
const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api/v1";

export default function Page() {
  const [orgs, setOrgs] = useState([]); const [selected, setSelected] = useState("org-almaty-1");
  const [forecast, setForecast] = useState(null); const [anomalies, setAnomalies] = useState([]); const [error, setError] = useState("");
  useEffect(() => { fetch(`${API}/organizations`).then(r => r.json()).then(setOrgs).catch(() => setError("API unavailable")); }, []);
  useEffect(() => { Promise.all([fetch(`${API}/forecasts/${selected}`).then(r => r.json()), fetch(`${API}/anomalies?organization_id=${selected}`).then(r => r.json())]).then(([f,a]) => {setForecast(f); setAnomalies(a);}).catch(() => setError("Analytics unavailable")); }, [selected]);
  return <main><header><p className="eyebrow">EARLY WARNING & FORECASTING</p><h1>MedFlow AI</h1><p>Прототип поддержки решений. Не даёт диагнозов и не заменяет экспертную проверку.</p></header>
    <aside className="notice">SYNTHETIC DEMO — данные сгенерированы для демонстрации; это не данные Министерства здравоохранения.</aside>
    {error && <p className="error">{error}</p>}<section className="grid"><article><h2>Медицинская организация</h2><select value={selected} onChange={e => setSelected(e.target.value)}>{orgs.map(o => <option key={o.id} value={o.id}>{o.name} · риск: {o.risk}</option>)}</select><p>Текущая очередь: <strong>{orgs.find(o => o.id === selected)?.latest_waiting ?? "—"}</strong></p></article>
    <article><h2>Прогноз ожидающих</h2>{forecast ? <><strong className="number">{forecast.expected}</strong><p>на {forecast.horizon_days} дней; интервал: {forecast.lower}—{forecast.upper}</p><small>Модель: {forecast.model}</small></> : "Загрузка…"}</article>
    <article><h2>Факторы модели</h2>{forecast?.contributors.map(x => <p key={x}>{x}</p>)}<small>Факторы — вклад в аналитический прогноз, а не причинный вывод.</small></article></section>
    <section><h2>Аномалии очереди</h2>{anomalies.length ? anomalies.map(a => <article className="anomaly" key={a.date}><strong>{a.severity.toUpperCase()} · {a.date}</strong><p>{a.explanation}</p><p>Наблюдение {a.observed}; reference {a.expected}; z-score {a.z_score}</p></article>) : <p>Статистических сигналов не найдено.</p>}</section>
    <footer>Human-in-the-loop: перед любым действием аналитик обязан сопоставить сигнал с первичными данными и контекстом организации.</footer></main>;
}
