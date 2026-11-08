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
