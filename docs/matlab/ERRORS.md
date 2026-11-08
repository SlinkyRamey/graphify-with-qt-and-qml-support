# MATLAB and Simulink diagnostics

The structural reader owns the diagnostic code, severity, source context and
recovery advice. The CLI and watcher stop before publication when the
`SIMULINK_INCOMPLETE` guard reports a fatal model or MATLAB source error.
A model package with missing or invalid required parts contributes no partial
facts. A corrected input is retried through ordinary extraction.

| Diagnostic family | Severity and boundary | Recovery |
| --- | --- | --- |
| `MATLAB_UNSUPPORTED` | Informational. No recognized MATLAB declarations or script syntax | Confirm file type or configure its language remap |
| `MATLAB_PARSER_MISSING`, `MATLAB_PARSER_LOAD` | Error in optional MATLAB grammar admission | Install or reinstall `graphifyy[matlab]` |
| `MATLAB_READ`, `MATLAB_ROOT`, `MATLAB_SYNTAX`, `MATLAB_LIMIT` | Error while admitting or parsing source | Repair the source, root, or size and retry |
| `SIMULINK_READ`, `SIMULINK_ROOT`, `SIMULINK_SOURCE_IDENTITY` | Error opening a saved model under the admitted root | Repair file access and retry |
| `SIMULINK_PACKAGE_*`, `SIMULINK_LIMIT` | Error admitting a ZIP catalog, relationship, member or resource budget | Use a complete supported SLX package |
| `SIMULINK_JSON_INVALID`, `SIMULINK_XML_INVALID`, `SIMULINK_XML_UNSAFE`, `SIMULINK_STRUCTURE` | Error interpreting required saved parts | Save or repair the model in a supported format |
| `SIMULINK_SIGNAL_*` | Warning for a saved signal whose port pair cannot be represented or proved | Inspect the indicated member and save the corrected model |
| `SIMULINK_SYSTEM_CYCLE`, `SIMULINK_STATEFLOW_STRUCTURE` | Error for conflicting required system or chart bodies | Repair the saved hierarchy and retry |
| `SIMULINK_PORT_MAPPING`, `SIMULINK_STATEFLOW_OWNER`, `SIMULINK_STATEFLOW_DECLARATION` | Warning for an unproved boundary or chart owner/declaration | Inspect saved ownership and resave |
| `MATLAB_ROOT_MISMATCH` | Update attempted with different scan and publication roots | Run from the project root or use its absolute path |

Normal accepted sources produce located facts and no failure diagnostic.
Unmatched literal dependencies remain evidence on their source nodes. The
normal graph writer owns I/O failures and retry; the source reader does not
publish or roll back graph files.

Product attribution is additive and uses the same admitted facts and
publication guard. Unmatched and shadowed names do not emit product claims
or errors. The original saved reference remains available when present.
No catalog download, license, tool installation, or native analysis is
needed at runtime. Malformed input and write failures follow the existing
diagnostics and retention policy above.
