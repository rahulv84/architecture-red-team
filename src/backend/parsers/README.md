# backend/parsers

Turns uploaded files into a uniform `ParsedDocument`. Parsers only read content (safe YAML loaders,
`defusedxml`, no evaluation or execution) and never infer structure that is not present.

## Purpose and responsibility

* Detect the file type from the extension and validate magic bytes.
* Dispatch to the right parser and enforce size / zip / YAML-alias limits.
* Report what could not be extracted as warnings (never fabricate structure).

## Modules

| File | Functions | Handles |
| --- | --- | --- |
| `parser_registry.py` | `safe_filename`, `detect_type`, `default_role`, `parse_bytes`, `EXTENSION_TYPES`, `SUPPORTED_EXTENSIONS` | Type detection and dispatch |
| `types.py` | `ParsedDocument`, `ParserLimits`, `DocSection`, `DiagramNode`, `DiagramEdge`, `IacResource` | Shared output types |
| `document_parser.py` | `parse_text`, `parse_docx`, `parse_pptx`, `decode_text`, `sections_from_markdown`, `sections_from_plain_text` | TXT/MD, DOCX (headings, tables), PPTX (slides, notes) |
| `pdf_parser.py` | `parse_pdf` | Text layer per page via `pypdf`; low-text pages flagged for vision, embedded images extracted (max 12) |
| `image_parser.py` | `parse_image` | Validates PNG/JPEG/WebP, records size; `requires_vision=True`, no structure |
| `diagram_parser.py` | `parse_drawio`, `parse_mermaid`, `parse_plantuml`, `parse_svg` | Nodes/edges; SVG yields text labels only |
| `iac_parser.py` | `parse_terraform`, `parse_structured`, `load_structured` | Terraform (python-hcl2), CloudFormation (YAML/JSON incl. short-form tags), Kubernetes (multi-document, `List`), Docker Compose, OpenAPI, generic YAML/JSON |

## Supported extensions (`parser_registry.EXTENSION_TYPES`)

| Detected type | Extensions | Parser | Default role |
| --- | --- | --- | --- |
| `pdf` | `.pdf` | `parse_pdf` | SUPPORTING |
| `markdown` | `.md`, `.markdown` | `parse_text(markdown=True)` | SUPPORTING |
| `text` | `.txt`, `.rst`, `.log`, `.csv`, `.ini`, `.conf`, `.toml` | `parse_text(markdown=False)` | SUPPORTING |
| `docx` / `pptx` | `.docx` / `.pptx` | `parse_docx` / `parse_pptx` | SUPPORTING |
| `image` | `.png`, `.jpg`, `.jpeg`, `.webp` | `parse_image` | DIAGRAM |
| `svg` | `.svg` | `parse_svg` | DIAGRAM |
| `drawio` | `.drawio`, `.xml` (only if it contains `mxfile`/`mxGraphModel`) | `parse_drawio` | DIAGRAM |
| `mermaid` | `.mmd`, `.mermaid` | `parse_mermaid` | DIAGRAM |
| `plantuml` | `.puml`, `.plantuml`, `.pu` | `parse_plantuml` | DIAGRAM |
| `terraform` | `.tf`, `.tfvars` | `parse_terraform` | INFRASTRUCTURE |
| `structured` | `.yaml`, `.yml`, `.json` | `parse_structured` (decides `cloudformation`, `kubernetes`, `compose`, `openapi`, else `yaml`/`json`) | SUPPORTING by detected type (the `kind` is only known after parsing) |

Signatures are checked for PDF (`%PDF-`), DOCX/PPTX (`PK`) and images (PNG/JPEG/WebP). Unknown
extensions raise `UnsupportedFileError`.

## `ParsedDocument`

`kind`, `text`, `sections[]` (heading, page/slide, line range), `nodes[]`/`edges[]` (diagrams),
`resources[]` (`IacResource`: framework, resource_type, name, address, properties, line, namespace),
`metadata`, `warnings[]`, `page_count`, `requires_vision`, `image_paths[]`. It is serialised to
`data/uploads/<project>/<artifact>/parsed.json` by `IngestionService`.

## Inputs and outputs

* Input: `bytes`, filename, detected type, `ParserLimits` (25 MB, 200 PDF pages, 150 MB / 5000 zip
  entries, 2,000,000 text characters, 100 YAML aliases, 5000 diagram nodes by default; the first two
  come from `Settings`).
* Output: `ParsedDocument`; failures raise `ParseError` (HTTP 422), which the ingestion service
  stores as `parse_status = FAILED` on the artifact instead of rejecting the upload.

## Dependencies

`core.exceptions`; third-party: `pypdf`, `python-docx`, `python-pptx`, `Pillow`, `python-hcl2`,
`PyYAML`, `defusedxml`. Consumed by `IngestionService`, `ContextService` (type only),
`ArchitectureService`, `tools.*` (`IacResource`).

## Role in application execution

Upload (`POST /projects/{id}/documents`) -> `IngestionService.add_document` -> `parse_bytes`.
During a review the graph does not re-parse files: `parse_documents` loads `parsed.json` through
`ReviewCache.docs`, and `extract_architecture` converts `resources`/`nodes`/`sections` to an
`ArchitectureModel`.

## Extension points

To add a file type (for example Bicep or a new diagram format):
1. Write `parse_<type>(data, filename, limits) -> ParsedDocument` in a new or existing module;
   raise `ParseError` for corrupt input and use `decode_text` for text.
2. Add the extension to `EXTENSION_TYPES`, optionally a signature check in `_SIGNATURES` and a
   default role in `_DOCUMENT_TYPE_DEFAULT_ROLE`.
3. Add a branch to `parse_bytes`.
4. If it declares infrastructure: emit `IacResource` objects, add a resource-type map in
   `services/architecture_service.py` (`TF_TYPE_MAP`/`CFN_TYPE_MAP`) and write rules in
   `backend/tools/`.
5. Add tests under `tests/unit/test_ingestion_and_parsers.py`.

## Limitations

* Raster images and scanned PDFs are not interpreted without a vision-capable live provider; there
  is no OCR.
* SVG connectors are anonymous paths, so only labels are extracted.
* Terraform modules are recorded as a single `module` resource; module contents and variable values
  are not resolved. `.tfvars` values are only recorded in metadata.
* Images embedded in DOCX/PPTX are counted and flagged (`requires_vision`, warning); they are not extracted, and
  `ArchitectureService._vision` reports them as a LIMITATION note. Standalone image files and images extracted from
  PDFs (first 4 per document) are analysed when the provider supports vision.
