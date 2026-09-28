# Privacy classification and output boundary

The local raw extracts may contain health information and person-level rows. They are not synthetic and must be treated as sensitive even where obvious direct identifiers are absent.

| Class | Observed examples | Runtime policy |
|---|---|---|
| Direct/persistent identifiers | `patient_seq_no`, hospitalization codes | raw/interim only; never API/log/model feature |
| Quasi-identifiers | timestamps, diagnosis/ICD, age/residency/insurance attributes where present | raw/interim only unless a steward approves a sufficiently aggregated statistic |
| Organization/region identifiers | destination organization code, origin region code | allowed only at organization/day aggregation |
| Aggregate measures | daily registration count, current snapshot count, lags/rolling history | explicit processed/API allow-list |
| Sensitive clinical/financial fields | diagnosis names/codes, benefit/insurance/finance attributes, amounts | excluded from the current mart and all exports |

`data_pipeline.PUBLIC_FIELDS` is the serialization boundary for the processed mart. API responses and exports use narrower explicit schemas. Raw/interim files are ignored by Git, excluded from images, absent from runtime mounts and never logged. Access, retention, minimum-cell-size rules and re-identification review still require approval by the data owner before real public operation; aggregation alone is not a guarantee of anonymity. Differential privacy is not applied because no validated privacy budget or use-case requirement exists.
