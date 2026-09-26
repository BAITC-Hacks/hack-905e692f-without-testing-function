export function ModelInformation({metrics,t}) {
const m=metrics?.metrics, period=metrics?.train_period, validation=metrics?.validation_period;
return <section className="panel model-info" id="model"><p className="eyebrow">{t.transparency}</p><h2>{t.info}</h2><div className="model-grid">
<article><span>Модель / версия</span><b>{metrics?.algorithm||"Ridge Regression"} · {metrics?.model_version||"—"}</b></article>
<article><span>Что прогнозируется</span><b>Новые регистрации в очередь за день</b></article>
<article><span>Дата обучения</span><b>{metrics?.trained_at?new Date(metrics.trained_at).toLocaleString():"—"}</b></article>
<article><span>Train / validation</span><b>{period?.[0]||"—"} → {period?.[1]||"—"}<br/>{validation?.[0]||"—"} → {validation?.[1]||"—"}</b></article>
<article><span>Качество на хронологической validation</span><b title="Единицы — регистрации/день">MAE {m?.mae??"—"} · RMSE {m?.rmse??"—"}</b><small>WAPE {m?.wape??"—"}%{m?.wape==null?" · артефакт обучен до добавления метрики":""}</small></article>
<article><span>Baseline: значение предыдущего дня</span><b title="MAE на том же validation периоде">MAE {m?.baseline_mae??"—"}</b></article></div>
<div className="global-importance"><h3>Глобальные факторы (модуль коэффициента)</h3>{metrics?.global_importance?.slice(0,6).map(x=><span key={x.feature} title="Зависит от масштаба признака и не доказывает причинность">{x.feature}: {x.importance}</span>)}</div>
<p className="model-note">Ограничения: один снимок очереди, а не история её размера. Регистрации восстановлены из записей, оставшихся в снимке: это не полный поток поступлений. Регион — регион происхождения пациентов, не местонахождение МО. Интервалы приближённые. Вклады и коэффициенты описывают связи, а не причины.</p></section>
}
