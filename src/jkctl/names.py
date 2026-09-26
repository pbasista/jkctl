"""What the web page calls each setting, and what it says the setting is.

JK's datasource labels are kept as they are -- :mod:`jkctl.registers` says
why: a label that matches JK's own screen is one somebody can find there --
but as the name a first-time reader is given they do not work.  "Vol. Cell
RCV" says "voltage" twice (the unit already says it) and never says what RCV
is; "Continued Charge Curr." is the charge over-current protection under a
name nobody would guess; one card said "Start Balance Volt." and the band
beside it "balancing starts above this".

So each setting has a *name* here -- short, in the protection's own acronym
where it has one, since the acronyms are what the manual, the app and the
installer all use -- and a *description* that spells the acronym out and
says what the setting does.  The page shows the name wherever the setting
appears, on every card and in every plan, and the description, with JK's
label and the register's key, when the name is hovered.  The command line
prints the same name, with the key and JK's label beside it
(:attr:`jkctl.registers.Register.title`), so neither calls a setting
something the other does not.

A register missing from here keeps JK's label for its name and has no
description: the page is no worse than it was.
"""

from __future__ import annotations

NAMES: dict[str, tuple[str, str]] = {
    # --- cell voltages -----------------------------------------------------
    "volCellOV": (
        "Cell OVP",
        "Cell over-voltage protection (OVP): a cell reaching this stops charging.",
    ),
    "volCellOVPR": (
        "Cell OVPR",
        "Cell over-voltage protection release (OVPR): charging is allowed again once "
        "every cell is back below this.",
    ),
    "volCellUV": (
        "Cell UVP",
        "Cell under-voltage protection (UVP): a cell falling to this stops discharging.",
    ),
    "volCellUVPR": (
        "Cell UVPR",
        "Cell under-voltage protection release (UVPR): discharging is allowed again once "
        "every cell is back above this.",
    ),
    "volSysPwrOff": (
        "Power-off voltage",
        "A cell falling to this turns the board off altogether.",
    ),
    "volSmartSleep": (
        "Smart sleep voltage",
        "Below this cell voltage, with nothing flowing, the board goes to sleep once the "
        "smart sleep time has passed.",
    ),
    "timeSmartSleep": (
        "Smart sleep time",
        "How long the pack has to sit idle below the smart sleep voltage before the board "
        "sleeps.",
    ),
    # --- charging targets --------------------------------------------------
    "volSOCP100": (
        "SOC 100%",
        "State of charge (SOC) 100% voltage: the cell voltage the charge reading counts "
        "as full.",
    ),
    "volSOCP0": (
        "SOC 0%",
        "State of charge (SOC) 0% voltage: the cell voltage the charge reading counts as "
        "empty.",
    ),
    "volCellRCV": (
        "Cell RCV",
        "Request charge voltage (RCV), per cell: the absorption voltage an inverter is "
        "asked to charge to.",
    ),
    "volCellRFV": (
        "Cell RFV",
        "Request float voltage (RFV), per cell: the float voltage an inverter is asked to "
        "hold once absorption is over.",
    ),
    "rcvTime": (
        "RCV time",
        "Request charge voltage (RCV) time: how long an inverter is asked to hold the "
        "absorption voltage.",
    ),
    "rfvTime": (
        "RFV time",
        "Request float voltage (RFV) time: how long an inverter is asked to hold the float "
        "voltage.",
    ),
    # --- currents and their timing -----------------------------------------
    "timBatCOC": (
        "Charge OCP",
        "Charge over-current protection (OCP): the most current the pack may take "
        "continuously. Above it for longer than the charge OCP delay, charging stops.",
    ),
    "timBatCOCPDly": (
        "Charge OCP delay",
        "How long the charge current may stay above the charge OCP before charging stops.",
    ),
    "timBatCOCPRDly": (
        "Charge OCPR time",
        "Charge over-current protection release (OCPR) time: how long charging stays off "
        "after the charge OCP tripped.",
    ),
    "timBatDcOC": (
        "Discharge OCP",
        "Discharge over-current protection (OCP): the most current the pack may deliver "
        "continuously. Above it for longer than the discharge OCP delay, discharging stops.",
    ),
    "timBatDcOCPDly": (
        "Discharge OCP delay",
        "How long the discharge current may stay above the discharge OCP before "
        "discharging stops.",
    ),
    "timBatDcOCPRDly": (
        "Discharge OCPR time",
        "Discharge over-current protection release (OCPR) time: how long discharging stays "
        "off after the discharge OCP tripped.",
    ),
    "scpDelay": (
        "SCP delay",
        "Short-circuit protection (SCP) delay: how long a short circuit is tolerated before "
        "discharging is cut. A hardware comparator, hence microseconds.",
    ),
    "timBatSCPRDly": (
        "SCPR time",
        "Short-circuit protection release (SCPR) time: how long discharging stays off "
        "after a short circuit.",
    ),
    "dischrgPreChrgT": (
        "Precharge time",
        "Discharge precharge time: how long the load's capacitors are charged through the "
        "precharge resistor before discharging is switched on.",
    ),
    "currentRange": (
        "Current range",
        "JK's datasource calls this Current Range and says nothing more about it.",
    ),
    # --- temperatures ------------------------------------------------------
    "tmpBatCOT": (
        "Charge OTP",
        "Charge over-temperature protection (OTP): a battery probe reaching this stops "
        "charging.",
    ),
    "tmpBatCOTPR": (
        "Charge OTPR",
        "Charge over-temperature protection release (OTPR): charging is allowed again once "
        "the probes are back below this.",
    ),
    "tmpBatDcOT": (
        "Discharge OTP",
        "Discharge over-temperature protection (OTP): a battery probe reaching this stops "
        "discharging.",
    ),
    "tmpBatDcOTPR": (
        "Discharge OTPR",
        "Discharge over-temperature protection release (OTPR): discharging is allowed "
        "again once the probes are back below this.",
    ),
    "tmpBatCUT": (
        "Charge UTP",
        "Charge under-temperature protection (UTP): a battery probe falling to this stops "
        "charging. Charging a lithium cell below freezing damages it.",
    ),
    "tmpBatCUTPR": (
        "Charge UTPR",
        "Charge under-temperature protection release (UTPR): charging is allowed again "
        "once the probes are back above this.",
    ),
    "tmpBatDCHUT": (
        "Discharge UTP",
        "Discharge under-temperature protection (UTP): a battery probe falling to this "
        "stops discharging.",
    ),
    "tmpBatDCHUTPR": (
        "Discharge UTPR",
        "Discharge under-temperature protection release (UTPR): discharging is allowed "
        "again once the probes are back above this.",
    ),
    "tmpMosOT": (
        "MOS OTP",
        "MOSFET over-temperature protection (OTP): the board's power switches reaching "
        "this turn charging and discharging off.",
    ),
    "tmpMosOTPR": (
        "MOS OTPR",
        "MOSFET over-temperature protection release (OTPR): the switches are turned back "
        "on once they have cooled below this.",
    ),
    "tmpStartHeating": (
        "Heater on",
        "Heating start temperature: below this the heater output is switched on.",
    ),
    "tmpStopHeating": (
        "Heater off",
        "Heating stop temperature: at this the heater output is switched off again.",
    ),
    # --- balancing ---------------------------------------------------------
    "balanEn": ("Balancing", "Whether the balancer may work on the cells at all."),
    "volBalanTrig": (
        "Balance trigger",
        "The spread between the highest and the lowest cell at which the balancer starts.",
    ),
    "volStartBalan": (
        "Balance start",
        "Balance start voltage: the balancer only works while the cells are above this.",
    ),
    "curBalanMax": (
        "Max balance current",
        "The most current the balancer moves from one cell to another.",
    ),
    "cellConWireRes": (
        "Wire resistance",
        "Connection wire resistance: each sense wire's resistance, used to correct that "
        "cell's voltage for the drop along its wire.",
    ),
    # --- the pack and the board --------------------------------------------
    "batChargeEn": (
        "Charging",
        "Charging enabled. Off, the charge switch stays open and the pack takes no charge.",
    ),
    "batDischargeEn": (
        "Discharging",
        "Discharging enabled. Off, the discharge switch stays open and the pack powers "
        "nothing.",
    ),
    "cellCount": (
        "Cell count",
        "How many cells are wired in series. It has to match the pack: the board only "
        "protects the cells it knows about.",
    ),
    "capBatCell": (
        "Capacity",
        "Battery capacity: the design capacity the state of charge is worked out against.",
    ),
    "devAddr": (
        "Device address",
        "The address this board answers on. On most boards the DIP switches set it.",
    ),
    "switchStatus": (
        "Multiplexed switches",
        "Sixteen on/off settings kept as the bits of one word.",
    ),
    # --- ports and outputs -------------------------------------------------
    "uart1ProtoNo": ("UART1 protocol", "The protocol the first serial port speaks."),
    "uart2ProtoNo": ("UART2 protocol", "The protocol the second serial port speaks."),
    "canProtoNo": ("CAN protocol", "The protocol the CAN port speaks."),
    "lcdBuzzerTrigger": (
        "Buzzer trigger",
        "What makes the buzzer sound: a condition from JK's list.",
    ),
    "lcdBuzzerTriggerVal": (
        "Buzzer on at",
        "The value of the buzzer's trigger condition at which it starts sounding.",
    ),
    "lcdBuzzerReleaseVal": (
        "Buzzer off at",
        "The value of the buzzer's trigger condition at which it stops again.",
    ),
    "dry1Trigger": (
        "Dry contact 1 trigger",
        "What closes the first dry contact: a condition from JK's list.",
    ),
    "dry1TriggerVal": (
        "Dry contact 1 on at",
        "The value of the trigger condition at which the first dry contact closes.",
    ),
    "dry1ReleaseVal": (
        "Dry contact 1 off at",
        "The value of the trigger condition at which the first dry contact opens again.",
    ),
    "dry2Trigger": (
        "Dry contact 2 trigger",
        "What closes the second dry contact: a condition from JK's list.",
    ),
    "dry2TriggerVal": (
        "Dry contact 2 on at",
        "The value of the trigger condition at which the second dry contact closes.",
    ),
    "dry2ReleaseVal": (
        "Dry contact 2 off at",
        "The value of the trigger condition at which the second dry contact opens again.",
    ),
    "dataStoredPeriod": (
        "Logging interval",
        "How often the board stores a record of the pack in its own history.",
    ),
}


def title_of(key: str, label: str) -> str:
    """Return the name the page shows for a register: ours, else JK's label."""
    return NAMES[key][0] if key in NAMES else label


def description_of(key: str) -> str | None:
    """Return what the page says a register is, if it says anything."""
    return NAMES[key][1] if key in NAMES else None
