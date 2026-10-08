import csv
import io
import json
from pathlib import Path

import pandas as pd
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class ParseError(ValueError):
    """An expected user-facing ingestion failure."""


def read_csv(path: Path, *, preserve_text: bool = False) -> pd.DataFrame:
    raw = path.read_bytes()
    if not raw or b"\x00" in raw or raw.startswith(b"%PDF-"):
        raise ParseError("文件不是有效的 CSV 文本，或文件为空。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("CSV 仅支持 UTF-8 / UTF-8 BOM，请转换编码后重新上传。") from exc
    try:
        reader = csv.reader(io.StringIO(text), strict=True)
        header = next(reader)
        if not header or any(not name.strip() for name in header):
            raise ParseError("CSV 列名不能为空。")
        if len(set(header)) != len(header):
            raise ParseError("CSV 存在重复列名，请修正后上传。")
        rows = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise ParseError(f"CSV 第 {reader.line_num} 行列数与表头不一致。")
            rows += 1
        if not rows:
            raise ParseError("CSV 没有数据行。")
        options = {"dtype": str, "keep_default_na": False} if preserve_text else {}
        frame = pd.read_csv(io.StringIO(text), **options)
        if frame.empty:
            raise ParseError("CSV 没有数据行。")
        return frame
    except (
        StopIteration,
        csv.Error,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as exc:
        raise ParseError("CSV 格式无法解析，请检查分隔符、引号和数据行。") from exc


def csv_metadata(frame: pd.DataFrame) -> dict:
    missing = int(frame.isna().sum().sum())
    warnings = ["字段类型和缺失值为自动推断，原文预览保留文本；尚未进行业务字段校验或日期转换。"]
    non_finite = int(
        frame.select_dtypes(include="number").isin([float("inf"), float("-inf")]).sum().sum()
    )
    if non_finite:
        warnings.append(
            f"存在 {non_finite} 个无穷数值，原文预览保留文本；缺失值统计不包含这些数值。"
        )
    return {
        "row_count": len(frame),
        "columns": [
            {
                "name": str(name),
                "dtype": str(frame[name].dtype),
                "missing_count": int(frame[name].isna().sum()),
            }
            for name in frame.columns
        ],
        "missing_cells": missing,
        "duplicate_rows": int(frame.duplicated().sum()),
        "warnings": warnings,
    }


def read_pdf(path: Path) -> PdfReader:
    with path.open("rb") as file:
        signature = file.read(8)
    if not signature.startswith(b"%PDF-"):
        raise ParseError("文件内容不是 PDF。")
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ParseError("暂不支持加密 PDF，请解密后上传。")
        if not reader.pages:
            raise ParseError("PDF 没有页面。")
        return reader
    except (PdfReadError, ValueError, OSError) as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError("PDF 已损坏或无法解析。") from exc


def extract_pdf_text(page) -> str:
    try:
        return page.extract_text() or ""
    except Exception as exc:
        raise ParseError("PDF 文本提取失败，请检查文件。") from exc


def inspect_file(path: Path, kind: str) -> dict:
    if kind == "csv":
        return csv_metadata(read_csv(path))
    reader = read_pdf(path)
    counts = [len(extract_pdf_text(page).strip()) for page in reader.pages]
    if not any(counts):
        raise ParseError("PDF 未提取到文本，可能是扫描件；第一阶段不支持 OCR。")
    return {
        "page_count": len(counts),
        "text_char_count": sum(counts),
        "pages_without_text": counts.count(0),
    }


def csv_preview(path: Path, page: int, page_size: int) -> dict:
    frame = read_csv(path, preserve_text=True)
    subset = frame.iloc[(page - 1) * page_size : page * page_size]
    # 原文定位使用逻辑单元格文本，保留前导零、NA 字面值和金额写法；空单元格为 ""。
    return {
        "columns": list(frame.columns),
        "rows": json.loads(subset.to_json(orient="records")),
        "total_rows": len(frame),
        "page": page,
        "page_size": page_size,
    }
