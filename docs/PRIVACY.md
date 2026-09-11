# Privacy

The demo uses synthetic aggregates only. Raw-data profiling flags likely direct identifiers by column-name patterns (IIN, patient, name/FIO, phone, email, address). Such fields are excluded from canonical aggregate facts, logs, API responses and UI. Before production use, a data steward must approve mappings, retention, access controls and aggregation rules.
