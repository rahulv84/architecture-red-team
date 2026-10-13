# backend/reporting/templates

Status: intentionally empty. No template files are required at runtime.

## Why it is empty

The PDF report is built programmatically with ReportLab (`backend/reporting/pdf_generator.py`):
layout, styles and tables are Python code (`SimpleDocTemplate`, `Paragraph`, `Table`). ADR Markdown
is produced by an f-string in `adr_generator.render_markdown`. No templating engine (Jinja or
similar) is installed or used, so nothing reads from this directory.

## What the folder is reserved for

* Template files if a templating engine is introduced later (for example an HTML report template
  or a customised ADR Markdown template such as MADR).
* Static report assets (logo, style sheet) if the PDF or an HTML export needs branding.

## Inputs, outputs, dependencies

None today. Nothing in `backend/` references this path.

## Role in application execution

None. Report generation path: `finalize` -> `ReportService.generate` ->
`reporting.json_generator` / `reporting.pdf_generator`.

## Extension points

If templates are added, load them relative to `Path(__file__).parent / "templates"` from the
generator module, keep them out of `data/` (which is runtime state), and add tests that render a
template with a fixture `AssessmentReport`.

## Limitations

Files placed here have no effect until a generator is changed to read them.
