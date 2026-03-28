from mars_agent.orchestration import MissionPhase, MissionPhaseStateMachine, ReadinessSignals


def test_state_machine_transitions_through_core_phases() -> None:
    machine = MissionPhaseStateMachine()

    phase = machine.next_phase(
        MissionPhase.TRANSIT,
        ReadinessSignals(
            landing_site_validated=True,
            surface_power_stable=False,
            life_support_stable=False,
            resource_surplus_ratio=0.8,
        ),
    )
    assert phase is MissionPhase.LANDING

    phase = machine.next_phase(
        MissionPhase.LANDING,
        ReadinessSignals(
            landing_site_validated=True,
            surface_power_stable=True,
            life_support_stable=True,
            resource_surplus_ratio=1.0,
        ),
    )
    assert phase is MissionPhase.EARLY_OPERATIONS

    phase = machine.next_phase(
        MissionPhase.EARLY_OPERATIONS,
        ReadinessSignals(
            landing_site_validated=True,
            surface_power_stable=True,
            life_support_stable=True,
            resource_surplus_ratio=1.3,
        ),
    )
    assert phase is MissionPhase.SCALING
