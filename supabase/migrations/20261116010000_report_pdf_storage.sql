-- Each report points to the exact object uploaded for the PDF viewer.
alter table public.report_landings
    add column if not exists pdf_storage_path text,
    add column if not exists pdf_sha256 text;
