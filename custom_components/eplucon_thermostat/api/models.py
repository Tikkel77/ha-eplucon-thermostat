from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Module:
    id: int
    account_module_index: str
    name: str
    type: str


@dataclass
class Zone:
    module_id: int
    module_name: str
    account_module_index: str
    zone_api_id: int
    zone_internal_id: int
    mode_id: int
    schedule_id: int
    name: str
    mode: str
    set_temperature_c: float
    current_temperature_c: Optional[float]
    schedule_index: int
    const_temp_time: int
    raw_data: Dict[str, Any]

    @property
    def set_temperature_deci(self) -> int:
        return int(round(self.set_temperature_c * 10))


@dataclass
class FormInput:
    name: str
    value: str
    input_type: str = ""
    checked: bool = False


@dataclass
class ProgramForm:
    index: int
    schedule_id: int
    mode_id: int
    zone_id: int
    action_url: str
    schedule_name: str = ""
    inputs: List[FormInput] = field(default_factory=list)

    def serialize_pairs(self) -> List[tuple[str, str]]:
        pairs: List[tuple[str, str]] = []
        for item in self.inputs:
            if item.input_type == "checkbox" and not item.checked:
                continue
            pairs.append((item.name, item.value))
        return pairs
