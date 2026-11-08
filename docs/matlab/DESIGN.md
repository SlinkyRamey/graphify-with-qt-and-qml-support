# MATLAB and Simulink design

## ADR-MATLAB-001 — Analyze saved source offline

The MATLAB reader uses the MATLAB grammar in the existing pinned
`tree-sitter-language-pack==0.11.0`. The `matlab` extra selects that package.
`qml` and `all` already select it. Objective-C retains the visible directive
branch for shared `.m` files, `.mm` retains its existing route, and explicit
remaps take precedence. MATLAB statements must pass the grammar and source
admission checks. The parser records immediate ownership, exact spelling and
source locations before building conservative lexical relationships. Package
and private path segments constrain the corpus lookup. Ambiguous indexing,
runtime object dispatch and computed names have no resolved targets.

The Simulink reader uses bounded ZIP, JSON and XML readers in the Python standard
library. It follows literal internal Open Packaging Conventions relationships
to the diagram and its saved systems. The older XML model/library and newer
JSON diagram with split XML systems both contribute the same fact shapes.
Archive members remain in memory. Member paths, expansion, counts, depth,
duplicate identities and unsafe XML constructs are checked before accepting a
file. A model, its blocks and ports own stable identities within the saved
archive. Directed signal edges start at the saved source port and reach every
supported destination. Subsystem boundary edges require unique saved ports.

A supported Stateflow machine/instance record anchors its saved chart to a
unique block path. Explicitly identified MATLAB function declarations within
a chart are retained with archive member provenance. Stateflow execution and
transition evaluation remain outside this profile.
[Stateflow MATLAB functions](https://www.mathworks.com/help/stateflow/api/stateflow.emfunction.html)

MathWorks documents the compressed model package, the model reference contract,
and the difference between saved source and active variant selection. The
precise JSON and XML member layout is an observed file profile and is tested
with synthetic packages. MATLAB calls and model callbacks are never executed.
[Save Models](https://www.mathworks.com/help/simulink/ug/save-models.html) ·
[Function declarations](https://www.mathworks.com/help/matlab/ref/function.html) ·
[MATLAB name precedence](https://www.mathworks.com/help/matlab/matlab_prog/function-precedence-order.html) ·
[Model references](https://www.mathworks.com/help/simulink/slref/find_mdlrefs.html)

The upstream MATLAB proposal informed review of lexical call boundaries. This
implementation uses the language pack already shipped by the fork and adds its
own model pipeline. [Graphify upstream PR #4836](https://github.com/Graphify-Labs/graphify/pull/4836)

## ADR-MATLAB-002 — Resolve against the admitted corpus

`extract_matlab` and `extract_simulink` produce file-owned facts independently.
The extraction facade separates those facts before generic language resolution
and rejoins the resulting nodes and edges before graph assembly. Saved references
remain literal metadata. The separate resolver emits inferred edges only for
eligible unique targets. For example, a library link uses the saved path within
one admitted library and a model reference uses its exact file name. A literal
MATLAB S-function name or supported workspace initializer can identify an admitted
MATLAB declaration. Missing toolboxes, dynamic expressions and ambiguous names
remain unresolved. Workspace references respect the saved source type. Display sanitization follows lookup.
[Model workspace sources](https://www.mathworks.com/help/simulink/ug/specify-source-for-data-in-model-workspace.html)

The AST cache salts the content key with a language-specific epoch, effective
suffix, root-relative file owner and explicit remap identity. Thus identical
bytes in two files cannot borrow each other's names or scopes. The existing
global AST epoch and unrelated cache formats remain unchanged. When inputs are
added, modified, removed or excluded, the existing extract and watcher paths
refresh the admitted MATLAB/Simulink slice using warm per-file syntax caches.
They do not borrow model source candidates from a previous graph. Fatal source
diagnostics prevent publication before graph/manifest updates even when the
requested refresh otherwise accepts shrinkage. Successful input files can retain
their independent syntax cache entries.

Lookup covers literal saved references, source-visible declarations and a bounded
set of supported static call shapes. Analysis does not establish compiled port
types, active variants, evaluated dictionary values, arbitrary MATLAB path
precedence or numerical behavior. Use a root-scoped update for these project IDs.
See the [diagnostic contract](ERRORS.md) and [acceptance matrix](PLAN.md).

## Shared seams

The extractor facade retains dispatch, aggregation and source-tier stamping.
The CLI and watcher retain discovery, failure gating and publication. Their
MATLAB additions are one registry/cache boundary, one language partition and a
refresh/guard at the existing output owner. The existing owners remain subject to these increment ceilings. Cohesive
extraction of their responsibilities remains separate under their migration
boundaries.


| File | Owner | Base / increment ceiling (physical lines) | Reason |
| --- | --- | ---: | --- |
| `graphify/extract.py` | Extractor facade | 11,978 / 12,012 | Dispatch, raw-cache key and source partition |
| `graphify/detect.py` | Discovery | 3,248 / 3,251 | Archive admission and word-count bypass |
| `graphify/cli.py` | CLI publication | 5,575 / 5,582 | Existing incremental selection and failure gate |
| `graphify/watch.py` | Watcher publication | 3,444 / 3,439 | Language refresh and guard; semantic document selection moves to a pure helper |
| `tests/test_extract.py` | Extractor regressions | 6,631 / 6,638 | Existing shared-suffix contract |
| `tests/test_languages.py` | Language fixture contracts | 6,408 / 6,441 | Mixed-source ownership on warm cache |

The watcher still owns the read and failure policy. The pure document helper
receives the accepted graph and source identity adapter and returns the same
selection before incremental dispatch. Existing semantic-document watcher
regressions characterize its role and ordering.
