"""
theta_files_storage.py
Company-scoped Excel library for NSCC Theta Sheets (Browse Theta Sheets).

Default storage is Azure Blob (same locally and on App Service).
Set THETA_FILES_STORAGE=local only for offline dev without Azure credentials.

Blob/key layout:  {company_id}/{file_id}__{filename}
"""

from __future__ import annotations

import io
import os
import uuid
from datetime import datetime, timezone

_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
THETA_FILES_FOLDER = 'ThetaFiles'
STORAGE_NAME_SEP = '__'
ALLOWED_EXTENSIONS = {'.xlsx', '.xls', '.csv', '.xlsm'}


def _uid(company_id) -> str | None:
    if not company_id:
        return None
    return str(company_id).strip() or None


def _storage_mode() -> str:
    explicit = (os.getenv('THETA_FILES_STORAGE') or '').strip().lower()
    if explicit == 'local':
        return 'local'
    return 'blob'


def _local_company_dir(company_id: str) -> str:
    path = os.path.join(_APP_ROOT, THETA_FILES_FOLDER, company_id)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or '').strip()
    if not base or base in ('.', '..'):
        raise ValueError('Invalid filename')
    if '..' in base or '/' in base or '\\' in base:
        raise ValueError('Invalid filename')
    ext = os.path.splitext(base)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError('Only Excel/CSV files are allowed (.xlsx, .xls, .csv, .xlsm)')
    return base


def _build_storage_name(file_id: str, filename: str) -> str:
    return f"{file_id}{STORAGE_NAME_SEP}{filename}"


def _parse_storage_name(storage_name: str) -> tuple[str, str] | None:
    if STORAGE_NAME_SEP not in storage_name:
        return None
    file_id, filename = storage_name.split(STORAGE_NAME_SEP, 1)
    if not file_id or not filename:
        return None
    return file_id, filename


def _blob_container_client():
    conn = (
        os.getenv('THETA_FILES_BLOB_CONNECTION_STRING')
        or os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        or ''
    ).strip()
    if not conn:
        raise RuntimeError('Azure Blob connection string is not configured')
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:
        raise RuntimeError(
            'azure-storage-blob is required for blob storage. pip install azure-storage-blob'
        ) from e
    container = (os.getenv('THETA_FILES_CONTAINER') or 'theta-files').strip()
    client = BlobServiceClient.from_connection_string(conn)
    container_client = client.get_container_client(container)
    try:
        container_client.create_container()
    except Exception:
        pass
    return container_client


def list_theta_files(company_id: str) -> list[dict]:
    cid = _uid(company_id)
    if not cid:
        return []

    entries: list[dict] = []
    if _storage_mode() == 'blob':
        prefix = f"{cid}/"
        container = _blob_container_client()
        for blob in container.list_blobs(name_starts_with=prefix):
            rel = blob.name[len(prefix):] if blob.name.startswith(prefix) else blob.name
            parsed = _parse_storage_name(rel)
            if not parsed:
                continue
            file_id, filename = parsed
            modified = blob.last_modified
            if modified and modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            entries.append({
                'id': file_id,
                'filename': filename,
                'size_bytes': blob.size or 0,
                'modified_at': modified.isoformat() if modified else None,
            })
    else:
        folder = _local_company_dir(cid)
        for name in os.listdir(folder):
            fpath = os.path.join(folder, name)
            if not os.path.isfile(fpath):
                continue
            parsed = _parse_storage_name(name)
            if not parsed:
                continue
            file_id, filename = parsed
            stat = os.stat(fpath)
            entries.append({
                'id': file_id,
                'filename': filename,
                'size_bytes': stat.st_size,
                'modified_at': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    entries.sort(key=lambda e: e.get('modified_at') or '', reverse=True)
    return entries


def save_theta_file(company_id: str, filename: str, data: bytes) -> dict:
    cid = _uid(company_id)
    if not cid:
        raise ValueError('company_id is required')
    safe_name = _safe_filename(filename)
    file_id = str(uuid.uuid4())
    storage_name = _build_storage_name(file_id, safe_name)

    if _storage_mode() == 'blob':
        container = _blob_container_client()
        blob_name = f"{cid}/{storage_name}"
        container.upload_blob(blob_name, data, overwrite=True)
    else:
        folder = _local_company_dir(cid)
        dest = os.path.join(folder, storage_name)
        with open(dest, 'wb') as f:
            f.write(data)

    now = datetime.now(timezone.utc).isoformat()
    return {
        'id': file_id,
        'filename': safe_name,
        'size_bytes': len(data),
        'modified_at': now,
    }


def replace_theta_file(
    company_id: str,
    file_id: str,
    data: bytes,
    filename: str | None = None,
) -> dict | None:
    """Overwrite an existing library workbook in place (same file id)."""
    cid = _uid(company_id)
    fid = _uid(file_id)
    if not cid or not fid:
        raise ValueError('company_id and file_id are required')
    if not data:
        raise ValueError('Empty file')

    info = get_theta_file_info(cid, fid)
    if not info:
        return None
    existing_name = info.get('filename') or 'workbook.xlsx'
    safe_name = _safe_filename(filename or existing_name)
    storage_name = _build_storage_name(fid, safe_name)
    same_name = safe_name == existing_name

    if _storage_mode() == 'blob':
        container = _blob_container_client()
        blob_path = f"{cid}/{storage_name}"
        if not same_name:
            prefix = f"{cid}/{fid}{STORAGE_NAME_SEP}"
            for blob in container.list_blobs(name_starts_with=prefix):
                container.delete_blob(blob.name)
        container.upload_blob(blob_path, data, overwrite=True)
    else:
        folder = _local_company_dir(cid)
        for name in os.listdir(folder):
            if name.startswith(f"{fid}{STORAGE_NAME_SEP}"):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
        with open(os.path.join(folder, storage_name), 'wb') as f:
            f.write(data)

    now = datetime.now(timezone.utc).isoformat()
    return {
        'id': fid,
        'filename': safe_name,
        'size_bytes': len(data),
        'modified_at': now,
    }


def get_theta_file_info(company_id: str, file_id: str) -> dict | None:
    """Return {id, filename} if the workbook exists, without downloading bytes."""
    cid = _uid(company_id)
    fid = _uid(file_id)
    if not cid or not fid:
        return None

    if _storage_mode() == 'blob':
        prefix = f"{cid}/{fid}{STORAGE_NAME_SEP}"
        container = _blob_container_client()
        for blob in container.list_blobs(name_starts_with=prefix):
            rel = blob.name.split('/', 1)[-1]
            parsed = _parse_storage_name(rel)
            if parsed and parsed[0] == fid:
                return {'id': fid, 'filename': parsed[1]}
        return None

    folder = _local_company_dir(cid)
    if not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        if not name.startswith(f"{fid}{STORAGE_NAME_SEP}"):
            continue
        parsed = _parse_storage_name(name)
        if parsed and parsed[0] == fid:
            return {'id': fid, 'filename': parsed[1]}
    return None


def read_theta_file(company_id: str, file_id: str) -> tuple[bytes, str] | None:
    cid = _uid(company_id)
    fid = _uid(file_id)
    if not cid or not fid:
        return None

    if _storage_mode() == 'blob':
        prefix = f"{cid}/{fid}{STORAGE_NAME_SEP}"
        container = _blob_container_client()
        for blob in container.list_blobs(name_starts_with=prefix):
            rel = blob.name.split('/', 1)[-1]
            parsed = _parse_storage_name(rel)
            if not parsed or parsed[0] != fid:
                continue
            downloader = container.download_blob(blob.name)
            return downloader.readall(), parsed[1]
        return None

    folder = _local_company_dir(cid)
    if not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        if not name.startswith(f"{fid}{STORAGE_NAME_SEP}"):
            continue
        parsed = _parse_storage_name(name)
        if not parsed:
            continue
        fpath = os.path.join(folder, name)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, 'rb') as f:
            return f.read(), parsed[1]
    return None


def delete_theta_file(company_id: str, file_id: str) -> bool:
    cid = _uid(company_id)
    fid = _uid(file_id)
    if not cid or not fid:
        return False

    if _storage_mode() == 'blob':
        prefix = f"{cid}/{fid}{STORAGE_NAME_SEP}"
        container = _blob_container_client()
        deleted = False
        for blob in container.list_blobs(name_starts_with=prefix):
            container.delete_blob(blob.name)
            deleted = True
        return deleted

    folder = _local_company_dir(cid)
    if not os.path.isdir(folder):
        return False
    deleted = False
    for name in os.listdir(folder):
        if name.startswith(f"{fid}{STORAGE_NAME_SEP}"):
            try:
                os.remove(os.path.join(folder, name))
                deleted = True
            except OSError:
                pass
    return deleted
