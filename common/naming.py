import re
import unicodedata


def normalize_name(name: str) -> str:
    """
    Standardizes names for IAM Groups, Users, and Tags to ensure AWS compatibility.

    Transformation logic:
    1. Transliterates local characters (e.g., 'Łódź' -> 'Lodz').
    2. Replaces spaces and whitespace with hyphens (-).
    3. Removes any character NOT allowed in AWS IAM: a-z, A-Z, 0-9, + = , . @ _ -
    4. Collapses multiple hyphens into one (e.g., 'A--B' -> 'A-B').
    5. Strips leading/trailing hyphens or underscores.

    Args:
        name (str): The raw input name (e.g., 'Grupa Łódź #1').

    Returns:
        str: A sanitized, AWS-safe name (e.g., 'Grupa-Lodz-1').
    """
    if not name:
        return ""

    normalized = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('utf-8')

    normalized = re.sub(r'\s+', '-', normalized)

    cleaned = re.sub(r'[^a-zA-Z0-9+=,.@_-]', '', normalized)

    cleaned = re.sub(r'-+', '-', cleaned)

    cleaned = cleaned.strip('-_')

    return cleaned