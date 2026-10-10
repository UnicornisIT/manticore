"""Pure helpers shared by spreadsheet/CSV import workflows."""

from __future__ import annotations

import os

import pandas as pd


def read_csv_dataframe(file_path):
    last_error = None
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            return pd.read_csv(file_path, sep=None, engine="python", encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise ValueError("Не удалось прочитать CSV в кодировке UTF-8 или Windows-1251")
    raise ValueError("Не удалось прочитать CSV-файл")


def read_tabular_upload(file_path):
    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".csv":
        return read_csv_dataframe(file_path)
    if extension == ".xls":
        return pd.read_excel(file_path)
    return pd.read_excel(file_path, engine="openpyxl")


def clean_upload_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def find_row_value_casefold(row, aliases):
    normalized = {str(key).strip().casefold(): key for key in row.index}
    for alias in aliases:
        key = normalized.get(str(alias).casefold())
        if key is not None:
            return row.get(key)
    return None


def find_dataframe_column_casefold(columns, aliases):
    normalized_aliases = {str(alias).strip().casefold() for alias in aliases}
    for column in columns:
        if str(column).strip().casefold() in normalized_aliases:
            return column
    return None
