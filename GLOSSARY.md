# Air Alert Analysis

Domain language for the KSE project: raion-level prediction of air-raid alert onsets in Ukraine, demonstrated as an accelerated historical replay on an interactive map. Analytical research prototype — not a warning system.

## Language

**Alert**:
An official air-raid alert state for a region, with a start and (optionally) an end. Since December 2025 alerts are issued at raion level; before that, at oblast level.
_Avoid_: siren, alarm, notification

**Alert Onset**:
The moment a region transitions from no alert to alert. The prediction target.
_Avoid_: alert start, activation

**Region**:
A raion (district) — the canonical spatial unit of this project. Oblast appears only as historical context (pre-December-2025 granularity) or as an aggregation level.
_Avoid_: area, zone, district (when used loosely)

**Horizon**:
A prediction lead time in minutes. The model family has three: 15, 30, 60.
_Avoid_: window, lookahead

**Attack Night**:
A time window (typically evening-to-morning) containing a coordinated mass drone/missile attack, used as an evaluation or replay unit.
_Avoid_: event, incident

**Replay**:
Accelerated playback of a historical attack night: the model scores each 5-minute step as if live, and the map renders predictions against what actually happened.
_Avoid_: simulation, demo mode

**Threat Event**:
A structured mention of an airborne threat extracted from a monitoring channel message: threat type, region(s), heading if stated, timestamp.
_Avoid_: sighting, report (ambiguous with Air Force Report)

**Air Force Report**:
The morning-after official summary from the Air Force Command: asset counts by type, launch origins, shootdowns, hit locations. Ground-truth cross-check, not a model input.
_Avoid_: summary, morning report (bare)

**Ground Truth**:
The recorded alert onsets from the historical dataset. Cross-checked against Air Force Reports and NASA FIRMS thermal data, but never replaced by them: alerts are a policy-driven proxy for threats, not a measurement.
_Avoid_: labels (too generic), actuals

**Monitoring Channel**:
A public Telegram channel publishing real-time threat sightings and vectors (e.g. @eRadarrua). Source of Threat Events via web-preview scraping.
_Avoid_: OSINT channel, source (ambiguous)

**Permanent Alert Region**:
A region excluded from modeling because its alert state is effectively continuous (e.g. Luhansk, Crimea in the source dataset; frontier raions may qualify by threshold).
_Avoid_: excluded region
