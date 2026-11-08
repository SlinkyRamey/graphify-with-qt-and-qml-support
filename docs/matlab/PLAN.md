# MATLAB and Simulink delivery plan

## INC-MATLAB-01 — Saved source and model structure

The increment targets the ordinary Graphify code pipeline and the two formats
present in the starting corpus, MATLAB `.m` and Simulink `.slx`. It builds
from the fork's delivered Qt/QML baseline so both extensions are available in
one checkout. The corpus informs validation and is kept outside the repository's
public fixtures. Support for legacy MDL, protected models, simulation, runtime
workspace evaluation, and arbitrary MATLAB path configuration is outside this
increment.

| Acceptance | Success evidence | Rejection or boundary evidence |
| --- | --- | --- |
| REQ-MATLAB-001-AC01 | Actual facade and discovery select MATLAB and retain Objective-C and remaps | Unsupported source and missing grammar do not create MATLAB facts |
| REQ-MATLAB-001-AC02 | Synthetic declarations plus corpus comparison of names, locations and owners | Strings, comments, indexing, lexical shadowing and case/scope boundaries |
| REQ-MATLAB-001-AC03/AC04 | Independent saved model structure and branch endpoint checks across archive layouts | Absent or malformed endpoints and unsupported records cannot create invented edges |
| REQ-MATLAB-002-AC01 | Unique admitted targets bind in a mixed corpus | Missing, ignored, protected, ambiguous and foreign-language targets cannot bind |
| REQ-MATLAB-002-AC02 | Cold/warm equivalence, moved-root provenance, persisted direction and target mutation | Obsolete links are removed when their authority disappears |
| REQ-MATLAB-002-AC03 | Real corrupt archive rejection and successful retry through graph update | Bounded ZIP/XML/JSON inputs, no partial cache record, previous graph bytes retained |

Verification proceeds from extractor fixtures to normal CLI and update paths,
then a read-only corpus comparison. Reports distinguish saved structure from
runtime behavior. Full tests, packaging and installed entry points use owned
outputs. MATLAB and Simulink execution is not required for this profile.

## INC-MATLAB-02 — Product attribution

This increment depends on the admitted MATLAB/Simulink cohort from
INC-MATLAB-01. It adds documented R2026b product evidence for the 12 products
selected by the starting corpus. The catalog records reviewed API spellings
and exact saved library paths. It does not define the installed MATLAB path
or a global inventory of every API in a product. Static matches and independent
MathWorks dependency analysis are distinct evidence.

| Acceptance | Success evidence | Rejection or boundary evidence |
| --- | --- | --- |
| REQ-MATLAB-003-AC01 | All 12 products via public extraction, exact source line and column, saved member, distinct same-line witnesses | Chart implementation and generic method spelling alone cannot identify a product |
| REQ-MATLAB-003-AC02 | Vendor call or block with no eligible local conflict | Local shadowing, strings/comments, unknown exact paths and ambiguous owners |
| REQ-MATLAB-003-AC03 | Cold/warm extraction, storage profiles, update and target changes | Real rejected input retains accepted graph; repair rebuilds exact witnesses |
| REQ-MATLAB-003-AC04 | Compare selected products and external library paths against the saved corpus and isolated MathWorks evidence | Separate fixture-only products, shared/missing ownership, dynamic or out-of-profile evidence |

The initial profile includes MATLAB, Simulink, Control System Toolbox, DSP
System Toolbox, Model Predictive Control Toolbox, Optimization Toolbox,
Parallel Computing Toolbox, Robotics System Toolbox, Simulink 3D Animation,
Simulink Control Design, Stateflow and Symbolic Math Toolbox. Product names
are public facts. Private corpus paths and source remain outside fixtures.
Exit requires the public, cache, update and corpus comparisons above. Any
native tooling limit is recorded separately.

## INC-MATLAB-03 — State-space constructor attribution

This increment depends on INC-MATLAB-02 and closes the native-certain
Control System Toolbox file discrepancy found in the reference corpus.
It extends REQ-MATLAB-003-AC05 with an exact four-positional constructor
profile. The feedthrough must provide explicit numeric source evidence.
Conversion forms, MPC options, name-value syntax, expanded arguments and
project overrides remain outside this inference. Generic response method
dispatch stays separate.

Verify the matrix shape, rejection controls, lexical and corpus
shadowing, warm cache and refresh through public entry points. Independently
compare all six reference source locations with the unchanged native
per-file report. Exit requires existing MATLAB/Simulink graph facts,
package profiles and source-relationship transport to remain usable.

## INC-MATLAB-04 — Hosted source validation

Linux retains the full all-extras suite. Windows and macOS use the native
source phase of the existing optional-wheel matrix. That phase verifies
the MATLAB grammar provided by the pinned language pack already installed
with the QML extra, then admits the dedicated MATLAB and Simulink files.
The clean core, clean QML and Linux artifact-only checks stay separate.
REQ-MATLAB-003-AC06 requires actual per-case evidence for each hosted
source lane, with any missing or skipped execution reported separately.
