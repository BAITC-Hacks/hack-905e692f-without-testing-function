# Canonical data model

`Region` → `MedicalOrganization` → `fact_hospital_period` where a verified organization identifier exists. `fact_region_period` is used when a source is region-only; it is never artificially joined to organizations. A period fact contains only observed aggregate measures: referrals, waiting, refusals and treated cases when the corresponding verified field exists.

`Forecast`, `Anomaly`, and `RiskAssessment` are derived analytical entities. A Load Pressure Index is not produced because no validated capacity/occupancy fields are present.
