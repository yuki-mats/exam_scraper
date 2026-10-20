"""Explicit identity of one official exam publication unit.

Original IDs can be shared across qualifications. These helpers never infer
qualification or exam year from an ID, and never change publication fields.
"""
from collections.abc import Mapping

UNIT_FIELDS = ("qualificationId", "examYear", "originalQuestionId")


def publication_unit(fields):
    if not isinstance(fields, Mapping):
        raise ValueError("explicit publication unit fields required")
    for key in ("qualificationId", "originalQuestionId"):
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("explicit publication unit identity required")
    year = fields.get("examYear")
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("official publication unit requires integer examYear")
    return {key: fields[key] for key in UNIT_FIELDS}


def active_unit_document_ids(entries, expected_unit):
    expected = publication_unit(expected_unit)
    if dict(expected_unit) != expected:
        raise ValueError("exact publication unit required")
    seen, result = set(), set()
    for identity, fields in entries:
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("unique publication membership document IDs required")
        seen.add(identity)
        if not isinstance(fields, Mapping):
            raise ValueError("masked publication membership fields required")
        if fields.get("isDeleted") is not False or fields.get("isChoiceOnly") is not False:
            continue
        observed = publication_unit(fields)
        if observed["originalQuestionId"] != expected["originalQuestionId"]:
            raise ValueError("publication membership query returned another original")
        if observed == expected:
            result.add(identity)
    return result
