import re


def normalize_name(name: str) -> str:
    """
    Standardizes names for IAM Groups, Users, and Tags to ensure AWS compatibility.

    Allowed characters in AWS IAM names and tags are usually:
    a-z, A-Z, 0-9, plus symbols: + = , . @ _ -

    This function removes any character that is NOT in this allowed set.
    It ensures that 'Lab Group 1' becomes 'LabGroup1' (or similar) to match AWS tags.

    Args:
        name (str): The raw input name (e.g., from user input).

    Returns:
        str: The sanitized name safe for AWS resources.
    """
    if not name:
        return ""

    # We remove any character that is NOT in the allowed set.
    # Note: We preserve underscores (_) and hyphens (-).
    return re.sub(r'[^a-zA-Z0-9+=,.@_-]', '', name)