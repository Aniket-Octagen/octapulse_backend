# Spreadsheet Save Performance Plan

Status: implemented (see Implementation notes at the end). Phase 1 analysis is from the code as of 2026-08-25.

## 1. Current Architecture

Theta Sheets has **two persistence stores** and **two editor modes**.

### Stores

1. **Library workbook (Azure Blob)** — `{company_id}/{file_id}__{filename}.xlsx` via `theta_files_storage.replace_theta_file`. API: `PUT /api/theta-files/<file_id>` (`replace_theta_file_route` in `app.py`). Frontend: `thetaFileService.replace` → `persistLibraryWorkbook` in `Dashboard.jsx`.

2. **Active company sheet (PostgreSQL JSONB)** — table `theta_sheets` (`data JSONB`, `version INTEGER`). API: `PUT /api/sheets/<sheet_id>` → `upsert_sheet` in `theta_sheet_db.py`. Frontend: `sheetService.saveSheet`.

Azure AI Foundry / Anthropic is **not** on either save path. It is used for chat, predictions, and other LLM routes in `app.py`.

There is **no WebSocket** for the grid. SSE `GET /api/sheets/active/events` exists (`_broadcast` after upsert) but the frontend **does not subscribe**.

### Edit → save flows

**A. Browse Theta Cloud library editor** (`Dashboard.jsx` ~6537)

- `SpreadsheetEditor` is mounted **without `sheetId`**.
- Comment in `SpreadsheetEditor.jsx`: omitting `sheetId` means `doSave` **no-ops** (no network).
- Univer already updates the cell immediately (`SheetValueChanged`).
- Persistence happens only when the user clicks **Save** (`handleThetaBrowserTransform`) or after **sheet-tab delete** (`handleThetaSheetDeleted`).
- Save does:
  1. `getGrid()` → `extractGridFromUniverWorkbook` (full workbook extract).
  2. `gridToXlsxFile` (SheetJS rebuild of entire .xlsx).
  3. `persistLibraryWorkbook` → `PUT` multipart file.
  4. Then `persistSelectedThetaSheets` → merge/validate → `PUT /api/sheets/:id` with **full grid JSON**.
  5. `setThetaEditorLoading(true)` for the whole sequence (footer “Saving…”).

**B. Dedicated live Theta Sheet editor** (`Dashboard.jsx` ~3140)

- `SpreadsheetEditor` **with `sheetId={activeSheetId}`**.
- Cell edit → `SheetValueChanged` → `markDirtyAndSave` → debounce **500ms** → `doSave`.
- `doSave` extracts the **entire** grid, `PUT /api/sheets/:id` with `{ data, version }`.
- Response includes full `data` JSONB plus `validation` (row-by-row `validate_sheet_data`).
- `onSaved` writes `saved.data` into `activeSheetData` / `liveSheetGridRef`.

Univer formulas stay in the client; the backend stores values/headers/rows JSON, not an Excel calc engine.

## 2. Current Bottleneck

Traced in code (not guessed):

| Location | What happens | Why it is slow |
| --- | --- | --- |
| `theta_files_storage.replace_theta_file` | Calls `read_theta_file` before write | **Downloads the entire existing xlsx from Azure** only to confirm existence and filename |
| Same function | `list_blobs` + `delete_blob` then `upload_blob` | Extra Azure round-trips when overwrite would suffice |
| `Dashboard.persistLibraryWorkbook` | `gridToXlsxFile` + multipart PUT | Full workbook rebuild and upload on every Save |
| `handleThetaBrowserTransform` | Library PUT **then** sheets PUT | Sequential waterfall; both full payloads |
| `setThetaEditorLoading(true)` | Overlay/disabled Save | User waits on both network calls |
| `SpreadsheetEditor.doSave` | `extractGridFromUniverWorkbook` | Full sheet extract (all sheets, all rows) |
| `sheetService.saveSheet` / `upsert_sheet` | Writes entire `data` JSONB | No cell/range patch |
| `update_sheet_route` | `jsonify(updated)` including `data` | Response echoes the full workbook |
| `Dashboard` `onSaved` | `setActiveSheetData(saved.data)` | Large React state update; not a Univer remount (`key={activeSheetId}`) but still heavy |
| `validate_sheet_data` | Scans every activity row | Extra CPU on every PUT; result unused to block save |

Not a bottleneck: Foundry on save; SSE (unused by UI); Univer cell paint (local).

## 3. Proposed Architecture

Keep two stores. Do **not** invent a cell-address PATCH against Blob (xlsx is a whole file). Optimize around:

```text
User edits cell
  → Univer paints immediately (already true)
  → pending dirty flag
  → debounce
  → persist:
       library: xlsx PUT without downloading old blob
       live sheet: JSONB PUT, response = version only
  → UI stays on current Univer snapshot
  → Saving… / Saved in footer (no full-grid overlay)

Azure AI Foundry: unchanged, off the save path
```

Cell-level JSON Patch is a later optional API; JSONB would still be a full-document write unless we split cells into rows. Out of scope for a non-breaking first cut.

## 4. API Changes

**Existing**

- `PUT /api/theta-files/<id>` — multipart `.xlsx`, full file.
- `GET/POST /api/sheets/active`, `GET/PUT /api/sheets/<id>` — full `data` object.

**Proposed (backward compatible)**

- `PUT /api/sheets/<id>` still accepts full `{ data, version }`.
- Response **omits `data`** (or sets `data: null`) and still returns `id`, `version`, `name`, `validation` optional.
- Clients that need the document keep using `GET /api/sheets/<id>` or `GET /api/sheets/active`.
- Current frontend already has the grid locally after extract; it must not require `saved.data`.

No new required endpoints for v1.

## 5. Database Changes

**None.** `theta_sheets.data` remains JSONB. No migrations.

`upsert_sheet` still replaces the JSON document (Postgres JSONB SET of the whole value). That is acceptable if the HTTP response and Azure download are removed from the hot path.

## 6. Frontend Changes

- `SpreadsheetEditor.doSave`: keep Univer snapshot; do not treat server `data` as source of truth after save.
- `Dashboard` `onSaved`: update **version only**.
- Library editor: debounce `persistLibraryWorkbook` from `onDirty` **without** `thetaEditorLoading` overlay (status text only).
- Explicit Save: `Promise.all` library + sheet persist when both apply; keep validation on sheet merge.
- Footer Saving… instead of blocking the whole editor if possible.

Optimistic UI: Univer already is optimistic. Do not rebuild Univer from GET after save.

Conflict: keep 409 + toast reload for version mismatch.

## 7. Azure AI Foundry Changes

**None.** Foundry is not invoked from `upsert_sheet`, `replace_theta_file`, or `SpreadsheetEditor.doSave`.

## 8. Failure and Recovery

- Network/backend: existing toasts; dirty flag stays true; debounce retries on next edit; explicit Save retries.
- 409: toast; user reloads (existing).
- Duplicate save: `savingRef` + version lock.
- Refresh while pending: library mode had no autosave (data loss until Save). Debounced library autosave reduces that. Dispose flush already exists when `sheetId` is set.
- AI failure: N/A for save.
- Partial batch: v1 still one full document per store, not cell batches.

## 9. Performance Targets

- UI cell update: immediate (Univer) — already met.
- Save request: drop Azure **download-before-save**; target typically **&lt;1s** for modest workbooks (not measured in CI). Stretch **&lt;300ms** only for JSONB-only autosave on small grids.
- No GET of full sheet after cell save.
- Payloads: still full grid/xlsx (store model), but **response** is small.
- Foundry must not block save — already true.

## 10. Files To Change

| File | Function | Change | Why | Risk |
| --- | --- | --- | --- | --- |
| `theta_files_storage.py` | `replace_theta_file` | Use `get_theta_file_info`; overwrite same blob when name unchanged | Remove full download + list/delete | Low if filename changes: still delete prefix |
| `app.py` | `update_sheet_route` | Strip `data` from JSON response | Smaller PUT | Clients that read `saved.data` |
| `Dashboard.jsx` | `onSaved`, Save/autosave | Version-only; debounce library persist; parallel saves; lighter loading | Perceived + real latency | Autosave races with Save |
| `SpreadsheetEditor.jsx` | `doSave`, `onDirty` | Don’t apply server grid; optional save status | Avoid extra React work | Low |
| `api.js` | `saveSheet` | No contract change | — | None |

## 11. Testing Plan

- Single cell + Save (library).
- Rapid edits (live sheet debounce).
- Copy/paste, undo (Univer local).
- Viewer cannot save (403).
- 409 other session.
- Refresh after autosave.
- Large sheet: save should not re-download blob.
- Foundry chat still independent.

## 12. Rollback Plan

Revert the four files above. PUT response again includes `data`. `replace_theta_file` again uses `read_theta_file`. No DB rollback.

---

## Implementation notes (Phase 2)

### What changed

- Library replace no longer downloads the previous xlsx.
- Same-filename blob upload uses overwrite without list/delete.
- Sheet PUT response omits `data`.
- Live editor `onSaved` updates version only.
- Library cell edits debounce-save the blob without the full-screen Saving overlay.
- Explicit Save runs library PUT and sheet PUT in parallel when both apply.

### Files modified

- `theta_files_storage.py`
- `app.py` (`update_sheet_route`)
- `src/pages/Dashboard.jsx`
- `src/components/SpreadsheetEditor.jsx`

### APIs modified

- `PUT /api/sheets/<id>` response: no `data` field (still `id`, `version`, `name`, `validation`, timestamps).

### Database changes

None.

### Performance

Not load-tested in CI. Expected win: **one fewer full-file Azure download per library save**.

### Tests performed

- Code-path review against save/replace/list.
- No automated e2e of Univer in this change.

### Known limitations

- Still sends **full** xlsx and **full** JSONB on each persist (not cell PATCH).
- Library autosave still rebuilds the whole xlsx in the browser (`gridToXlsxFile`).

### Rollback

Git revert the files listed above.
