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

@dataclass
class HeatpumpCommonInfo:
    spf: Any = None
    indoor_temperature: Any = None
    outdoor_temperature: Any = None
    brine_in_temperature: Any = None
    brine_out_temperature: Any = None
    configured_indoor_temperature: Any = None
    heating_in_temperature: Any = None
    heating_out_temperature: Any = None
    energy_usage: Any = None
    energy_delivered: Any = None
    import_energy: Any = None
    export_energy: Any = None
    ww_temperature: Any = None
    ww_temperature_configured: Any = None
    brine_pressure: Any = None
    cv_pressure: Any = None
    evaporation_temperature: Any = None
    condensation_temperature: Any = None
    inverter_temperature: Any = None
    compressor_speed: Any = None
    suction_gas_temperature: Any = None
    suction_gas_pressure: Any = None
    press_gas_temperature: Any = None
    press_gas_pressure: Any = None
    overheating: Any = None
    position_expansion_ventil: Any = None
    total_active_power: Any = None
    number_of_starts: Any = None
    operating_hours: Any = None
    operation_mode: Any = None
    heating_mode: Any = None
    dg1: Any = None
    sg2: Any = None
    sg3: Any = None
    sg4: Any = None
    warmwater: Any = None
    brine_circulation_pump: Any = None
    production_circulation_pump: Any = None
    act_vent_rpm: Any = None
    alarm_active: Any = None
    alarm_time: Any = None
    active_requests_ww: Any = None
    current_heating_pump_state: Any = None
    current_heating_state: Any = None
    # We allow extra args
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class HeatpumpRealtimeInfo:
    common: HeatpumpCommonInfo
    heatpump: Optional[Dict[str, Any]] = None


@dataclass
class HeatpumpHeatloadingStatus:
    heatloading_active: bool = False
    configurations: Dict[str, bool] = field(default_factory=dict)
    
    def __init__(self, heatloading_active=False, configurations=None, **kwargs):
        self.heatloading_active = heatloading_active
        self.configurations = configurations or {}
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class HeatpumpDevice:
    module_id: int
    account_module_index: str
    name: str
    realtime_info: Optional[HeatpumpRealtimeInfo] = None
    heatloading_status: Optional[HeatpumpHeatloadingStatus] = None
