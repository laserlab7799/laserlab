Forensic Laser Data

This folder contains the machine-readable forensic output for the laser simulation.

WHAT THE FORENSIC DATA REPRESENTS

The simulation tests every firing angle from 0.1° through 179.9° in 0.1° increments, for a total of 1,799 angles.

For each angle, the laser is modeled as a 3.0-second pulse divided into 0.1-second slices. The laser travels at 80 units per second, so each active slice can travel up to 8 units per simulation tick.

The simulation includes:

- vertical mirrors at x = -70, -10, 10, 70
- a horizontal mirror at y = 115
- moving and rotating blockers
- a rotating target centered at (0, 100)
- alternating one-unit mirror holes
- finite-segment collision testing for blockers and the target
- explicit world boundaries

Each firing angle starts from the same initial blocker positions and the same initial horizontal orientation of the blockers and target.


NUMERICAL RULES

The forensic replay uses the following numerical tolerances:

- mirror integer snap tolerance: 1e-10
- blocker/target segment endpoint tolerance: 1e-9
- general geometric epsilon: 1e-8

For mirror-hole classification, a computed mirror-intersection coordinate that falls within 1e-10 of an integer is snapped to that integer before the one-unit mirror band is classified.

Vertical mirrors are solid on even floor(y) bands and have holes on odd floor(y) bands.

The horizontal mirror is solid on odd floor(x) bands and has holes on even floor(x) bands.


MOVING BLOCKERS AND TARGET

Blockers move horizontally at 1 unit per second and are evaluated only at exact 0.1-second simulation ticks.

The blockers and target rotate clockwise with a 3.0-second rotation period. Since the simulation advances in 0.1-second ticks, this corresponds to 12° of clockwise rotation per tick.

For every tick, the current blocker locations, blocker endpoints, target endpoints, and rotation angles are calculated and recorded.


HOW EACH LASER SLICE IS SIMULATED

A new laser slice is emitted once per 0.1-second tick for the first 3.0 seconds, producing 30 slices.

Each slice begins at the origin with direction:

(cos(angle), sin(angle))

During each tick, an active slice has up to 8 units of travel available.

The simulator computes all relevant possible events within that travel distance:

1. intersection with a solid portion of a vertical mirror
2. intersection with a solid portion of the horizontal mirror
3. intersection with any blocker
4. intersection with the target
5. intersection with a world boundary

The nearest valid event is selected.


MIRROR HIT

For a vertical mirror hit, the x-component of the direction is reversed.

For a horizontal mirror hit, the y-component of the direction is reversed.

The slice remains active and continues using any distance left in that same 0.1-second tick. A tiny 1e-7 positional nudge is applied in the outgoing direction after reflection so that the same mirror surface is not immediately re-detected because of floating-point coincidence.


BLOCKER HIT

If a slice hits a blocker, that slice terminates at the recorded collision point.

Other slices in the pulse are independent. A later slice can therefore continue past that location if the moving blocker is no longer in its path.


TARGET HIT

If a slice hits the target, that slice terminates at the recorded target-intersection point and is marked as a target hit.

An angle is considered successful if at least one of its emitted slices hits the target.


BOUNDARY EXIT

If a slice reaches a world boundary before any blocker, target, or solid mirror event, that slice terminates at the boundary.


WHEN AN ANGLE SIMULATION ENDS

Laser emission stops after 30 ticks, corresponding to 3.0 seconds.

The simulation does not stop at that moment. Every slice already emitted continues to be propagated until it has terminated at:

- a blocker
- the target
- a world boundary

The angle run ends only after the 3-second emission period is complete and every emitted slice is inactive, subject to a safety limit of 2,000 ticks.


WHAT IS RECORDED FOR EACH ANGLE

Each angle has its own JSON file in the forensic_static/angles folder, named by firing angle, for example:

0.1.json
38.2.json
90.0.json
169.8.json

Each detailed angle record contains:

- angle_deg
- success
- target_hit_slice_count
- blocked_slice_count
- mirror_hit_count
- slice_count
- total_ticks
- simulation_end_time
- slice_outcomes
- detailed ticks


SLICE_OUTCOMES

For each emitted slice, the final record includes:

- emission tick
- whether it hit the target
- whether it hit a blocker
- blocker ID, if applicable
- number of mirror hits
- termination record
- final position
- final direction


TICKS

The detailed forensic replay also records the simulation tick by tick.

For each tick it stores:

- tick index
- simulation time
- blocker and target geometry for that tick
- the movement of every active laser slice
- the slice's starting point and direction
- available travel distance
- every relevant collision or mirror check considered
- the event selected as the nearest valid event
- the resulting endpoint of that substep

This makes the forensic output suitable for reconstructing and independently reviewing the trajectory rather than storing only the final success/failure result.


COLLISION CHECKS RECORDED IN THE AUDIT

For mirror checks, the forensic record includes information such as:

- raw intersection coordinate
- snapped coordinate
- whether integer snapping was applied
- integer mirror band
- whether that band was solid or a hole

For blocker and target checks, the audit records the ray/segment intersection calculation, including:

- ray distance parameter u
- segment parameter v
- whether the event lies within the current laser step
- whether the hit lies within the finite segment using the endpoint tolerance
- whether the collision candidate was accepted

The simulator then sorts valid event candidates by distance and selects the nearest one.


SUPPORTING FORENSIC FILES

The forensic_static folder also contains supporting JSON files used to describe and verify the full angle set, including configuration, angle-index, manifest, and verification information.

The verification data records the expected number of angles, the successful count, the unsuccessful count, and the complete successful-angle list.

Together, the per-angle JSON files and supporting forensic files provide a machine-readable audit trail for all 1,799 simulated firing angles.
