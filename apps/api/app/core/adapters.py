"""Schema-adaptive interfaces: mappings are recorded; source columns are never guessed."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar


@dataclass
class MappingReport:
    adapter: str
    compatible: bool
    mapped: dict[str, str]
    unmapped_source_fields: list[str]


class BaseDatasetAdapter(ABC):
    canonical_fields: ClassVar[set[str]] = set()

    @abstractmethod
    def detect(self, columns: list[str]) -> bool: ...

    def report(self, columns: list[str], mapping: dict[str, str]) -> MappingReport:
        return MappingReport(type(self).__name__, self.detect(columns), mapping, [c for c in columns if c not in mapping])


class HospitalReferralAdapter(BaseDatasetAdapter):
    canonical_fields: ClassVar[set[str]] = {"date", "region_id", "organization_id", "referrals"}
    def detect(self, columns: list[str]) -> bool: return bool(set(columns) & self.canonical_fields)


class WaitingListAdapter(HospitalReferralAdapter): pass
class RefusalAdapter(HospitalReferralAdapter): pass
class TreatedCasesAdapter(HospitalReferralAdapter): pass
